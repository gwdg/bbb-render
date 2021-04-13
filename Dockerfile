FROM ubuntu:20.04

ENV TZ=Europe/Berlin
RUN apt-get update && apt-get upgrade -y \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y \
        locales tzdata tini \
        python3-pip python3-dev build-essential \
        gir1.2-ges-1.0 ges1.0-tools libcairo2-dev libgirepository1.0-dev \
        gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    && rm -rf /var/lib/apt/lists/* \
    && localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8 \
    && pip3 install -U pip wheel
ENV LANG en_US.utf8

WORKDIR /app

COPY requirements.txt .
RUN pip3 install -r requirements.txt
COPY . .
ENTRYPOINT ["tini"]
