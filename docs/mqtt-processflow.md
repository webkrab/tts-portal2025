# 📡 Externe berichten – Architectuuroverzicht

Ons systeem ontvangt berichten via meerdere netwerken en protocollen, en verwerkt deze tot een gestandaardiseerd intern formaat. De verwerking is geoptimaliseerd voor snelheid door minimale verrijking met meta-data.

## 📥 Inkomende berichten

**Interfaces:**  
- TCP / UDP (zowel pull als push configuratie)  
- HTTP-requests (`GET` en `POST`)  

Deze berichten worden direct omgezet in een MQTT-bericht volgens onderstaand vast **inputtemplate**:

```python
MQTT input_message = {
    "raw": data,            # Origineel onbewerkt bericht
    "msgtype": msgtype,     # Type bericht, bijv. 'ais-nmea', 'adsb-sbs
    "msghash": msghash,     # Hash van de raw-data om duplicaten te voorkomen
    "received": received,   # Tijdstip van ontvangst in milliseconden
    "gateway": msggateway  # Identifier van de ontvangende gateway
}
```

**MQTT configuratie:**

- **Client naam:** `<datatype>-<protocol>_<gateway>`  
  *Bijvoorbeeld:* `ais-nmea_nl-deurne`
- **Topic:** `<datatype>/<gateway>/<protocol>`  
  *Bijvoorbeeld:* `ais/nl-deurne/nmea`

---

## 🔄 Interne verwerking

Verwerkingsclients lezen de `input_message`, en zetten deze om naar een standaard `internal_message`. Hierin zit zowel de ruwe data als een decodeerde versie.

```python
MQTT internal_message = {
    "raw": data,                              # Originele bericht
    "data": {msg_key: decoded_payload},       # Gestandaardiseerde ruwe data
    "formated": formated,                     # Gefilterde, geformatteerde data voor Tailor
    "msgtype": msgtype,                       # Overgenomen uit input
    "msghash": msghash,                       # Overgenomen uit input
    "received": received,                     # Overgenomen uit input
    "gateway": msg_gateway                    # Overgenomen uit input
}
```

**MQTT configuratie (intern):**

- **Client naam:** `<datatype>-<protocol>`  
  *Bijvoorbeeld:* `ais-nmea`
- **Topic:** `<datatype>/<gateway>/processed`  
  *Bijvoorbeeld:* `ais/nl-deurne/processed`

---

## 🧩 Verwerkingsclients

Er zijn drie typen interne verwerkers die elk een specifiek type bericht oppakken:

### 1. DB Receiver Client
- Verwerkt alleen het `formated` deel van berichten
- Slaat deze op in de **Tracker-database**

### 2. DB Message Client
- Verwerkt het `data` deel (gestandaardiseerde ruwe data)
- Slaat deze op in de **History-database**

### 3. Forwarder
- Verwerkt het originele `raw` bericht
- Slaat deze eveneens op in de **History-database**

---

## 📌 Samenvatting

| Stap         | Formaat           | Topic voorbeeld           | Client voorbeeld         |
|--------------|-------------------|----------------------------|---------------------------|
| Inkomend     | `input_message`   | `ais/nl-deurne/nmea`      | `ais-nmea_nl-deurne`      |
| Verwerking   | `internal_message`| `ais/nl-deurne/processed` | `ais-nmea`                |
| Opslag/Export| `formated`, `data`, `raw` | intern           | `db-receiver`, `db-message`, `forwarder` |
