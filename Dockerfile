FROM joyzoursky/python-chromedriver:3.8

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN export PATH=$PATH:/usr/lib/chromium-browser/

COPY . /app

VOLUME /config

CMD [ "python3", "-u", "flathunt.py", "-c", "/config/config.yaml" ]
