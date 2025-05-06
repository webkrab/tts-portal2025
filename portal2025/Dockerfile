# Stap 1: Kies een stabiele Python-image
FROM python:3.13-bookworm

# Zet Python output op unbuffered modus (belangrijk voor realtime logs)
ENV PYTHONUNBUFFERED=1

# Stap 2: Werk de systeeminstellingen bij en installeer vereiste system dependencies voor PostGIS
RUN apt-get update \
    && apt-get install -y \
    libpq-dev \
    binutils \
    gdal-bin \
    postgis \
    python3-gdal \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Stap 3: Stel de werkmap in binnen de container
WORKDIR /app

# Stap 4: Kopieer de requirements.txt naar de container
COPY requirements.txt /app/

# Stap 5: Installeer de Python-afhankelijkheden
RUN pip install --no-cache-dir -r requirements.txt

# Stap 6: Kopieer de rest van de projectbestanden naar de container
COPY . /app/

# Stap 7: Voeg een wait-for-it script toe om te wachten op de beschikbare services
COPY wait-for-it.sh /usr/local/bin/wait-for-it
RUN chmod +x /usr/local/bin/wait-for-it

# Stap 8: Expose de poort waarop de Django-app draait
EXPOSE 8000

# Stap 9: Start de Django-app met het wachtmechanisme voor MQTT of andere services
CMD ["wait-for-it", "$BROKER_IP:$BROKER_PORT", "--", "python", "manage.py", "runserver", "0.0.0.0:8000"]

