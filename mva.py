#!/usr/bin/env python3

import glob
import os
import paramiko
import re
import sys
import shutil
import time
import yaml


def upload_torrents(sftp):
    for torrent in glob.glob(f"{config['torrent_dir']}/*.torrent"):
        filename = torrent.split("/")[-1]
        print(f"Moving '{torrent}' to seedbox")
        sftp.put(
            torrent,
            f"/home/user/blackhole/{config['name']}/{filename}",
        )
        os.remove(torrent)


# Grabs files. They can be deleted after downloading
def download_files(sftp):
    files = sftp.listdir(f"/home/user/files/hs/{config['name']}/")
    for path in files:
        file = path.split("/")[-1]
        print(f"Downloading {file} from seedbox")
        if is_writing(sftp, path):
            print(f"skipping {file}. Is currently being written to disk")
            continue
        destination = get_plex_filename(file)
        if not_enough_space(sftp, path, destination):
            print(f"Can't download {file}. Not enough space on disk")
            continue
        sftp.get(path, destination)
        print(f"Cleaning up {file} from seedbox")
        sftp.remove(files)


def is_writing(sftp, file):
    first_read = sftp.lstat(file)
    time.sleep(1)
    second_read = sftp.lstat(file)
    return first_read.st_size != second_read.st_size


def not_enough_space(sftp, src, dest):
    dest_dir = dest[0:dest.rfind('/')]
    local_space = shutil.disk_usage(dest_dir).free
    remote_filesize = sftp.lstat(src).st_size
    return local_space > remote_filesize


def read_config():
    config = {}
    raw_config = {}
    config_files = [
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
    for dir in ['torrent_dir', 'plex_dir']:
        if raw_config[dir][-1] != "/":
            config[dir] = raw_config[dir] + "/"
        else:
            config[dir] = raw_config[dir]
        if not os.path.isdir(config[dir]):
            os.makedirs(config[dir])
    config_keys = [
        'anime',
        'name',
        'seedbox_url',
        'seedbox_user',
        'seedbox_pass',
        'verbose',
    ]
    for key in config_keys:
        config[key] = raw_config[key]
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
        'seedbox_url': "seedbox.domain.name:port",
        'seedbox_user': "username",
        'seedbox_pass': "password",
        'verbose': False,
        'plex_dir': "/path/to/anime/plex/dir",
        'torrent_dir': "/path/to/torrents"
    }
    config_file = f"{os.getenv('HOME')}/.config/mva/config.yaml"
    with open(config_file, "w") as handle:
        handle.write(yaml.dump(config))


def get_show_rule(config, name_pair):
    name = name_pair[0]
    ep = int(name_pair[1])
    for anime, data in config['anime'].items():
        if not contains_show_name(name, anime, data['seasons'].values()):
            continue
        for season_number, season_data in data['seasons'].items():
            ep_range = season_data['episodes']
            is_in_range = ep_range[0] <= ep and ep <= ep_range[1]
            is_name = name == anime or season_data['alias'] == name
            if is_in_range and is_name:
                path = f"{config['plex_dir']}{anime}/Season {season_number}/"
                file = f"{name} - s{int(season_number):02}e{int(ep):02}.mkv"
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
    print(split_name)
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
    print(split_name)
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
    print(f"{filename} can't be moved. No handling")
    return None


def print_verbose(*msg):
    if config['verbose']:
        print(*msg)


def main(argv):
    ssh = paramiko.client.SSHClient()
    global config
    config = read_config()
    for arg in argv:
        if arg == "-v":
            config['verbose'] = True
    if config['verbose']:
        print(config)
    ssh.connect(
        config['seedbox_url'],
        username=config['seedbox_user'],
        password=config['seedbox_pass']
    )
    sftp = ssh.open_sftp()
    upload_torrents(sftp)
    download_files(sftp)


if __name__ == "__main__":
    main(sys.argv[1:])
