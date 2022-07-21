FROM wiserain/flexget:3.3.17

COPY mva.py requirements.txt /app/
RUN pip install -r /app/requirements.txt

