#!/usr/bin/env python3

import discord
import glob
import os
import paramiko
import re
import sys
import shutil
import time
import traceback
import yaml


WEBHOOK_URL = ""

def upload_torrents(sftp):
    torrents = glob.glob(f"{config['torrent_dir']}/*.torrent")
    backup_dir = config["backup_dir"]
    rate_limit = config["rate_limit"]
    if len(torrents) > rate_limit:
        send_log_msg(" ".join([
            "needed to rate limit the torrents. Got",
            str(len(torrents)),
            "but we're only allowed to upload",
            str(rate_limit),
            "at a time",
        ]))
        torrents = torrents[0:rate_limit]
    for torrent in torrents:
        filename: str = torrent.split("/")[-1]
        # NOTE: This might end up being a bit fragile.
        # flexget makes the files like this: meta-[SubsPlease] Made in Abyss - Retsujitsu no Ougonkyou - 02 (1080p) [A386198C].mkv.torrent
        # Reading the contents of the torrent might be better.
        mkv_name = filename.replace("meta-","").replace(".torrent", "")
        show_name = get_plex_filename(mkv_name)
        if not show_name:
            send_log_msg(f"'{torrent}' wasn't able to be translated to a plex filename.")
            continue
        send_log_msg(f"Moving '{torrent}' to seedbox")
        sftp.put(
            torrent,
            f"/home/user/blackhole/{config['name']}/{filename}",
            callback=progress,
        )
        # Get the path we'll try to download it to
        backup_path = f"{backup_dir}/{show_name}/"
        try:
            os.makedirs(backup_path)
        except FileExistsError:
            print("didn't need to create a folder?")
        print(f"backing up torrent to {backup_path}")
        shutil.move(torrent, backup_path + filename)


# Grabs files. They can be deleted after downloading
def download_files(sftp):
    remote_base_dir=f"/home/user/files/hs/{config['name']}/"
    files = sftp.listdir(remote_base_dir)
    for file in files:
        path = remote_base_dir + file
        send_log_msg(f"Downloading {file} from seedbox")
        if is_writing(sftp, path):
            send_log_msg(f"skipping {file}. Is currently being written to disk")
            continue
        destination = get_plex_filename(file)
        if not destination:
            send_log_msg(f"@here couldn't find handler for {file}")
            continue
        if not_enough_space(sftp, path):
            send_log_msg(f"@here Can't download {file}. Not enough space on disk")
            continue
        last_percent = 0
        sftp.get(path, destination, callback=progress)
        print()
        print(f"Cleaning up {file} from seedbox")
        sftp.remove(path)


def is_writing(sftp, file):
    first_read = sftp.lstat(file)
    time.sleep(1)
    second_read = sftp.lstat(file)
    return first_read.st_size != second_read.st_size


def not_enough_space(sftp, src):
    local_space = shutil.disk_usage(config["plex_dir"]).free
    remote_filesize = sftp.lstat(src).st_size
    return local_space < remote_filesize


last_time = time.time()
last_speed = 0
last_percent = 0

def progress(current, total):
    global last_speed
    progress_percent = current/total * 100
    current_time = time.time()
    term_size = shutil.get_terminal_size(fallback=(120, 50))
    progress = f"{int(progress_percent)}%"
    speed = ((progress_percent - last_percent) * total / 100) / (current_time - last_time)
    fancy_speed = get_fancy_speed(speed + last_speed / 2)
    bar = ""
    bar_size = term_size.columns - len(progress) - len(fancy_speed) - 5
    for i in range(bar_size):
        if i > int(progress_percent/100 * bar_size):
            bar += " "
        elif i == int(progress_percent/100 * bar_size):
            bar += ">"
        else:
            bar += "="
    print(f"\r[{bar}] {progress} {fancy_speed} ", end="")
    last_speed=speed


def get_fancy_speed(speed_in_bps):
    if speed_in_bps > 10**9:
        return f"{speed_in_bps/10**9: >6.2f} GB/s"
    if speed_in_bps > 10**6:
        return f"{speed_in_bps/10**6: >6.2f} MB/s"
    if speed_in_bps > 10**3:
        return f"{speed_in_bps/10**3: >6.2f} KB/s"
    return f"{speed_in_bps: >6.2f}  B/s"


def read_config():
    config = {}
    raw_config = {}
    config_files = [
        "/config-yaml/mva.yaml",
        "/config-yaml/mva.yml",
        "/config/mva.yaml",
        "/config/mva.yml",
        "/etc/mva/config.yaml",
        "/etc/mva/config.yml",
        f"{os.getenv('HOME')}/.config/mva/config.yaml",
        f"{os.getenv('HOME')}/.config/mva/config.yml",
    ]
    for config_file in config_files:
        if os.path.exists(config_file):
            with open(config_file) as handle:
                raw_config = yaml.safe_load(handle)
                break
    else:
        print(
            "No config found! Please create a config at",
            " or ".join(config_files)
        )
        dump_template_config()
        print(
            "A template has been created for you at",
            f"{os.getenv('HOME')}/.config/mva/config.yaml",
        )
        sys.exit(1)
    for dir in ['torrent_dir', 'plex_dir', 'backup_dir']:
        if raw_config.get(dir) and raw_config[dir][-1] != "/":
            config[dir] = raw_config[dir] + "/"
        else:
            config[dir] = raw_config.get(dir)
        if not os.path.isdir(config[dir]):
            os.makedirs(config[dir])
    config_keys = [
        'anime',
        'name',
        'seedbox_host',
        'seedbox_port',
        'seedbox_user',
        'seedbox_pass',
        'rate_limit',
        'webhook_url',
        'verbose',
    ]
    for key in config_keys:
        config[key] = raw_config.get(key)
    global WEBHOOK_URL
    WEBHOOK_URL = config['webhook_url']
    config['verbose'] = False
    return config


def dump_template_config():
    config = {
        'anime': {
            "example anime": {
                "1": {
                    "episodes": [0, 13],
                },
                "2": {
                    "alias": "Anime-Name II",
                    "episodes": [0, 13],
                },
            },
            "other anime": {
                "1": {
                    "episodes": [0, 13],
                },
                "2": {
                    "episodes": [14, 26],
                },
            }
        },
        'name': "unique name",
        'seedbox_host': "seedbox.domain.name",
        'seedbox_port': "2222",
        'seedbox_user': "username",
        'seedbox_pass': "password",
        'verbose': False,
        'plex_dir': "/path/to/anime/plex/dir",
        'backup_dir': "/path/to/backup/dir",
        'torrent_dir': "/path/to/torrents/",
        'webhook_url': "https://discord.gg/.../",
        'rate_limit': 50,
    }
    config_file = f"{os.getenv('HOME')}/.config/mva/config.yaml"
    with open(config_file, "w") as handle:
        handle.write(yaml.dump(config))


def get_show_rule(config, name_pair):
    name = name_pair[0]
    if 'OVA' in name_pair[1]:
        print("Got an ova?")
        return None
    elif 'v' in name_pair[1]:
        ep = int(name_pair[1].split('v')[0])
    else:
        ep = int(float(name_pair[1]))
    for anime, data in config['anime'].items():
        if not contains_show_name(name, anime, data['seasons'].values()):
            continue
        for season_number, season_data in data['seasons'].items():
            ep_range = season_data['episodes']
            is_in_range = ep_range[0] <= ep and ep <= ep_range[1]
            is_name = name == anime or season_data['alias'] == name
            if is_in_range and is_name:
                path = f"{config['plex_dir']}{anime}/Season {season_number}/"
                file = f"{name} - s{int(season_number):02}e{int(ep - ep_range[0] + 1):02}.mkv"
                return {
                    'dir': path,
                    'file': file,
                }
    return None


def contains_show_name(search_string, anime, seasons):
    if anime != search_string:
        for season in seasons:
            if season['alias'] == search_string:
                return True
        return False
    else:
        return True


# Also puts in proper dir name!
# It's magic. Probably just leave it!
def cleanup_name_hs(name):
    tagreg = re.compile("\[(\w|\d)*\]")
    # An iter of all tags [*] in the name
    all_tags = tagreg.finditer(name)

    clean_name = name
    for tag in all_tags:
        # Remove tags
        clean_name = clean_name.replace(tag.group(0), "")
    # Remove .mkv
    clean_name = clean_name.replace(".mkv", "")
    split_name = clean_name.rsplit("-", 1)
    split_name[0] = split_name[0].strip()
    split_name[1] = split_name[1].strip()
    print_verbose(split_name)
    return split_name


# Subsplease is different!
def cleanup_name_sp(name):
    # They include resolutions in the name
    name = name.replace(' (1080p)', '')
    tagreg = re.compile('\[(\w|\d)*\]')
    # An iter of all tags [*] in the name
    all_tags = tagreg.finditer(name)
    clean_name = name
    for tag in all_tags:
        # Remove tags
        clean_name = clean_name.replace(tag.group(0), "")
    # Remove .mkv
    clean_name = clean_name.replace(".mkv", "")
    split_name = clean_name.rsplit("-", 1)
    split_name[0] = split_name[0].strip()
    split_name[1] = split_name[1].strip()
    print_verbose(split_name)
    return split_name


def get_plex_filename(filename):
    sources = {
        '[HorribleSubs]': cleanup_name_hs,
        '[SubsPlease]': cleanup_name_sp,
    }
    for tag, func in sources.items():
        if tag in filename:
            show = get_show_rule(config, func(filename))
            if show:
                if not os.path.exists(show['dir']):
                    print_verbose(
                        f"Made non-existent dir '{show['dir']}'"
                    )
                    os.makedirs(show['dir'])
                return show['dir'] + show['file']
            else:
                print(f"Skiping {filename}. No rule")
                return None
    print(f"{filename} can't be downloaded. No handling")
    return None


def print_verbose(*msg):
    if config['verbose']:
        print(*msg)


def send_log_msg(msg):
    if not WEBHOOK_URL:
        print("no webhook url!")
    else:
        webhook = discord.Webhook.from_url(WEBHOOK_URL, adapter=discord.RequestsWebhookAdapter())
        webhook.send(msg)
    print(msg)


def main(argv):
    ssh = paramiko.client.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.client.AutoAddPolicy)
    global config
    config = read_config()
    for arg in argv:
        if arg == "-v":
            config['verbose'] = True
    if config['verbose']:
        print(config)
    ssh.connect(
        config['seedbox_host'],
        port=config['seedbox_port'],
        username=config['seedbox_user'],
        password=config['seedbox_pass']
    )
    sftp = ssh.open_sftp()
    upload_torrents(sftp)
    download_files(sftp)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except:
        send_log_msg(f"@here **Script is _K I L L_**\n```\n{traceback.format_exc()}\n```")