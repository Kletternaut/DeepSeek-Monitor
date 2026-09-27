"""DeepSeek Monitor — internationalization (languages & currencies).

Contains language detection, the translation tables (``BASE_TRANSLATIONS`` /
``TRANSLATIONS``) and the currency symbols. No Tkinter dependency.

Public symbols:
    * :data:`LANGUAGES`, :data:`LANGUAGE_ORDER`
    * :func:`detect_language`
    * :data:`BASE_TRANSLATIONS`, :data:`TRANSLATIONS`
    * :data:`CURRENCY_SYMBOLS`
"""

from __future__ import annotations

import locale
import os

LANGUAGES = {
    "de": "Deutsch", "en": "English", "fr": "Français", "es": "Español",
    "it": "Italiano", "pt": "Português", "nl": "Nederlands",
    "pl": "Polski", "ru": "Русский", "zh": "中文",
}
LANGUAGE_ORDER = tuple(LANGUAGES)


def detect_language() -> str:
    values = [
        os.environ.get("LC_ALL", ""),
        os.environ.get("LC_MESSAGES", ""),
        os.environ.get("LANG", ""),
        os.environ.get("LANGUAGE", ""),
    ]
    try:
        values.append(locale.getlocale()[0] or "")
    except (ValueError, AttributeError):
        pass
    for value in values:
        value = (value or "").lower()
        for prefix in LANGUAGES:
            if value.startswith(prefix):
                return prefix
    return "en"


BASE_TRANSLATIONS = {
    "de": {
        "tariff":" Tarif ", "realtime":" Echtzeit ", "analytics":" Analytics ", "costs":"Kosten & Verbrauch",
        "tz":"TZ:", "weekend":"Wochenende:", "weekend_label":"Wochenende", "weekend_text":" Sa 00:00 – Mo 00:00 (Peking-Zeit)",
        "peak":"Peak:", "standard":"Std:", "refresh":"Jetzt aktualisieren",
        "api":"API", "api_key":"API-Key", "api_key_title":"DeepSeek API-Key",
        "api_key_hint":"API-Key eingeben oder neu setzen:", "save":"Speichern",
        "cancel":"Abbrechen", "remove":"Key entfernen",
        "env_hint":"DEEPSEEK_API_KEY wird ebenfalls unterstützt.",
        "connecting":"Verbinde mit DeepSeek API…", "error":"Fehler",
        "available":"verfügbar", "unavailable":"nicht ausreichend",
        "loaded":"Aufgeladen:", "granted":"Geschenkt:", "as_of":"Stand:",
        "analytics_title":"Verbrauchsanalyse",
        "analytics_hint":"Letzte 14 Tage. Verbrauch aus real aufgezeichneten Token-Usage-Daten des Proxys.",
        "total":"Gesamt", "normal":"Normal", "savings":"Pot. Ersparnis", "saving":"Ersparnis",
        "peak_share":"Peak-Anteil am Verbrauch:", "daily":"Tagesanalyse", "daily_title":"Tagesauswertung ({days} Tage)",
        "intervals":"Letzte Requests", "date":"Datum",
        "usage":"Gesamt", "weekend_short":"WE", "from":"Von", "to":"Bis",
        "zone":"Zone", "amount":"Verbrauch", "note":"Hinweis",
        "gap_note":"Schätzung bei größerer Messlücke",
        "analytics_note":"Annahme: Peak = {mult:.1f}× Standard. Die potenzielle Ersparnis zeigt die vermeidbare Peak-Prämie. Verbrauch aus realen Token-Usage-Daten des Proxys.",
        "no_data":"Noch keine Daten – die Auswertung baut sich mit jeder Abfrage auf.",
        "proxy_error_hint":"Keine Proxy-Daten – läuft der Dienst deepseek-proxy?",
        "no_key":"Kein API-Key hinterlegt.",
        "confirm_remove":"Gespeicherten API-Key wirklich löschen?",
        "no_key_error":"Kein API-Key: bitte über den API-Dialog setzen oder DEEPSEEK_API_KEY verwenden.",
        "peak_status":"PEAK", "standard_status":"STANDARD", "weekend_status":"WOCHENENDE OFF-PEAK",
        "zone_warning":"Tarifwechsel in ~{seconds} s → {status}",
        "language":"Sprache", "timezone":"Zeitzone", "auto":"Auto", "settings_title":"Einstellungen", "appearance":"Darstellung", "data":"Daten", "actions":"Aktionen", "on_top":"Immer im Vordergrund",
        "weekdays":["Mo","Di","Mi","Do","Fr","Sa","So"],
        "api_401":"API-Key ungültig oder abgelaufen.",
        "api_403":"Zugriff auf die DeepSeek API verweigert.",
        "api_429":"Zu viele API-Anfragen – bitte später erneut versuchen.",
        "poll_interval":"Balance-Abfrage", "poll_interval_hint":"Sekunden (mind. 20)", "apply":"Übernehmen", "saved":"Gespeichert ✓",
        "data_source":"Datenquelle", "local_proxy":"Lokal (Proxy/SQLite)", "influxdb":"InfluxDB (zentral)",
        "influx_url":"URL", "influx_token":"Token", "influx_org":"Organisation", "influx_bucket":"Bucket",
        "host":"Host", "all_hosts":"Alle Hosts",
    },
    "en": {
        "tariff":" Rate ", "realtime":" Realtime ", "analytics":" Analytics ", "costs":"Cost & Usage",
        "tz":"TZ:", "weekend":"Weekend:", "weekend_label":"Weekend", "weekend_text":" Sat 00:00 – Mon 00:00 (Beijing time)",
        "peak":"Peak:", "standard":"Std:", "refresh":"Refresh now",
        "api":"API", "api_key":"API key", "api_key_title":"DeepSeek API key",
        "api_key_hint":"Enter or replace API key:", "save":"Save",
        "cancel":"Cancel", "remove":"Remove key",
        "env_hint":"DEEPSEEK_API_KEY is also supported.",
        "connecting":"Connecting to DeepSeek API…", "error":"Error",
        "available":"available", "unavailable":"insufficient",
        "loaded":"Topped up:", "granted":"Granted:", "as_of":"As of:",
        "analytics_title":"Usage analysis",
        "analytics_hint":"Last 14 days. Usage from real recorded token usage data of the proxy.",
        "total":"Total", "normal":"Normal", "savings":"Potential saving", "saving":"Saving",
        "peak_share":"Peak share of usage:", "daily":"Daily analysis", "daily_title":"Daily breakdown ({days} days)",
        "intervals":"Latest requests", "date":"Date",
        "usage":"Total", "weekend_short":"WE", "from":"From", "to":"To",
        "zone":"Zone", "amount":"Usage", "note":"Note",
        "gap_note":"Estimate due to larger measurement gap",
        "analytics_note":"Assumption: Peak = {mult:.1f}× Standard. Potential saving is the avoidable peak premium. Usage from real token usage data of the proxy.",
        "no_data":"No data yet – the analysis builds with each poll.",
        "proxy_error_hint":"No proxy data – is the deepseek-proxy service running?",
        "no_key":"No API key configured.",
        "confirm_remove":"Really delete the saved API key?",
        "no_key_error":"No API key: set it in the API dialog or use DEEPSEEK_API_KEY.",
        "peak_status":"PEAK", "standard_status":"STANDARD", "weekend_status":"WEEKEND OFF-PEAK",
        "zone_warning":"Tariff change in ~{seconds} s → {status}",
        "language":"Language", "timezone":"Timezone", "auto":"Automatic", "settings_title":"Settings", "appearance":"Appearance", "data":"Data", "actions":"Actions", "on_top":"Always on top",
        "weekdays":["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
        "api_401":"API key invalid or expired.",
        "api_403":"DeepSeek API access denied.",
        "api_429":"Too many API requests – please try again later.",
        "poll_interval":"Balance polling", "poll_interval_hint":"seconds (min. 20)", "apply":"Apply", "saved":"Saved ✓",
        "data_source":"Data source", "local_proxy":"Local (proxy/SQLite)", "influxdb":"InfluxDB (central)",
        "influx_url":"URL", "influx_token":"Token", "influx_org":"Organization", "influx_bucket":"Bucket",
        "host":"Host", "all_hosts":"All hosts",
    },
}
TRANSLATIONS = {code: dict(BASE_TRANSLATIONS["en"]) for code in LANGUAGES}
TRANSLATIONS["de"].update(BASE_TRANSLATIONS["de"])
TRANSLATIONS["de"]["costs"] = "Kosten & Verbrauch"
TRANSLATIONS["en"]["costs"] = "Cost & Usage"
TRANSLATIONS["fr"]["costs"] = "Coûts & usage"
TRANSLATIONS["es"]["costs"] = "Costes y consumo"
TRANSLATIONS["it"]["costs"] = "Costi e consumo"
TRANSLATIONS["pt"]["costs"] = "Custos e consumo"
TRANSLATIONS["nl"]["costs"] = "Kosten & verbruik"
TRANSLATIONS["pl"]["costs"] = "Koszty i zużycie"
TRANSLATIONS["ru"]["costs"] = "Расходы и потребление"
TRANSLATIONS["zh"]["costs"] = "成本与用量"
# Weekday names for the realtime chart
for _lang, _days in {
    "de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "fr": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
    "es": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
    "it": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
    "pt": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
    "nl": ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"],
    "pl": ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"],
    "ru": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
    "zh": ["一", "二", "三", "四", "五", "六", "日"],
}.items():
    TRANSLATIONS[_lang]["weekdays"] = _days

# The most important visible UI terms are localized for the other main languages.
TRANSLATIONS["fr"].update({"costs":"Coûts & usage", "tariff":" Tarif ", "realtime":" Temps réel ", "analytics":" Analyse ", "refresh":"Actualiser", "api_key":"Clé API", "save":"Enregistrer", "cancel":"Annuler", "remove":"Supprimer", "language":"Langue"})
TRANSLATIONS["es"].update({"costs":"Costes y consumo", "tariff":" Tarifa ", "realtime":" Tiempo real ", "analytics":" Análisis ", "refresh":"Actualizar", "api_key":"Clave API", "save":"Guardar", "cancel":"Cancelar", "remove":"Eliminar", "language":"Idioma"})
TRANSLATIONS["it"].update({"costs":"Costi e consumo", "tariff":" Tariffa ", "realtime":" Realtime ", "analytics":" Analisi ", "refresh":"Aggiorna", "api_key":"Chiave API", "save":"Salva", "cancel":"Annulla", "remove":"Rimuovi", "language":"Lingua"})
TRANSLATIONS["pt"].update({"costs":"Custos e consumo", "tariff":" Tarifa ", "realtime":" Tempo real ", "analytics":" Análise ", "refresh":"Atualizar", "api_key":"Chave API", "save":"Guardar", "cancel":"Cancelar", "remove":"Remover", "language":"Idioma"})
TRANSLATIONS["nl"].update({"costs":"Kosten & verbruik", "tariff":" Tarief ", "realtime":" Realtime ", "analytics":" Analyse ", "refresh":"Vernieuwen", "api_key":"API-sleutel", "save":"Opslaan", "cancel":"Annuleren", "remove":"Verwijderen", "language":"Taal"})
TRANSLATIONS["pl"].update({"costs":"Koszty i zużycie", "tariff":" Taryfa ", "realtime":" Na żywo ", "analytics":" Analiza ", "refresh":"Odśwież", "api_key":"Klucz API", "save":"Zapisz", "cancel":"Anuluj", "remove":"Usuń", "language":"Język"})
TRANSLATIONS["ru"].update({"costs":"Расходы и потребление", "tariff":" Тариф ", "realtime":" Баланс ", "analytics":" Аналитика ", "refresh":"Обновить", "api_key":"API-ключ", "save":"Сохранить", "cancel":"Отмена", "remove":"Удалить", "language":"Язык"})
TRANSLATIONS["zh"].update({"tariff":" 费率 ", "realtime":" 实时 ", "analytics":" 分析 ", "costs":"成本与用量", "refresh":"刷新", "api_key":"API 密钥", "save":"保存", "cancel":"取消", "remove":"删除", "language":"语言"})

CURRENCY_SYMBOLS = {
    "USD":"$", "CNY":"¥", "EUR":"€", "GBP":"£", "JPY":"¥",
    "KRW":"₩", "CHF":"CHF ", "CAD":"C$", "AUD":"A$", "PLN":"zł ",
}
