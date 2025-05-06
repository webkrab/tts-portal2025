import logging
import os
import sqlite3
import inspect
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler

# Standaard logniveau (kan overschreven worden in productieconfig)
DEBUG_LEVEL = logging.DEBUG


class ClassNameFilter(logging.Filter):
    """
    Logging-filter die automatisch de class name toevoegt aan een logrecord (indien beschikbaar).

    Dit is handig voor objectgeoriënteerde debugging wanneer `self` beschikbaar is.

    Methods:
        filter(record): Voegt class_name toe als deze ontbreekt.
    """

    def filter(self, record):
        """
        Voegt het attribuut 'class_name' toe aan het logrecord indien niet aanwezig.

        Args:
            record (logging.LogRecord): Het logrecord dat gefilterd wordt.

        Returns:
            bool: True om het record door te laten.
        """
        if not hasattr(record, "class_name"):
            frame = inspect.currentframe()
            while frame:
                caller = frame.f_back
                if not caller:
                    break
                instance = caller.f_locals.get("self", None)
                if instance:
                    record.class_name = instance.__class__.__name__
                    break
                frame = caller
            else:
                record.class_name = "-"
        return True


class SQLiteHandler(logging.Handler):
    """
    Een logging handler die berichten opslaat in een SQLite-database met retentiebeheer.

    Args:
        db_path (str): Pad naar de SQLite database.
        retention_days (int): Aantal dagen dat logs bewaard worden.

    Methods:
        emit(record): Schrijft een logrecord naar de database.
        cleanup_old_logs(): Verwijdert oude logregels op basis van retentie.
    """

    def __init__(self, db_path="logs.db", retention_days=90):
        super().__init__()
        self.db_path = db_path
        self.retention_days = retention_days
        self._initialize_db()

    def _initialize_db(self):
        """
        Initialiseert de SQLite database en maakt de logs-tabel als deze nog niet bestaat.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    level TEXT,
                    app_name TEXT,
                    filename TEXT,
                    lineno INTEGER,
                    class_name TEXT,
                    func_name TEXT,
                    message TEXT
                )
            ''')
            conn.commit()

    def emit(self, record):
        """
        Schrijft een logrecord naar de SQLite database.

        Args:
            record (logging.LogRecord): Het logrecord dat opgeslagen moet worden.
        """
        log_time = datetime.now(timezone.utc).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO logs (timestamp, level, app_name, filename, lineno, class_name, func_name, message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                log_time,
                record.levelname,
                record.name,
                record.filename,
                record.lineno,
                getattr(record, "class_name", "-"),
                record.funcName,
                record.getMessage()
            ))
            conn.commit()

        self.cleanup_old_logs()

    def cleanup_old_logs(self):
        """
        Verwijdert logrecords die ouder zijn dan de ingestelde retentieperiode.
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        cutoff_str = cutoff_date.isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM logs WHERE timestamp < ?", (cutoff_str,))
            conn.commit()


def get_logger(name, log_file="logs/app.log", db_path="logs/app_logs.db"):
    """
    Configureert een logger die logt naar console, bestand én SQLite.

    Args:
        name (str): De naam van de logger, bijv. `__name__`.
        log_file (str): Pad naar het logbestand voor bestandsrotatie.
        db_path (str): Pad naar de SQLite database voor opslag van logs.

    Returns:
        logging.Logger: Een volledig geconfigureerde logger met drie handlers.

    Example:
        logger = get_logger(__name__)
        logger.info("Applicatie gestart.")
    """
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s/%(filename)s:%(lineno)d '
        '- [%(funcName)s] - %(message)s'
    )

    logger = logging.getLogger(name)
    logger.setLevel(DEBUG_LEVEL)

    if not logger.hasHandlers():
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # Bestand handler met rotatie (7 dagen)
        file_handler = TimedRotatingFileHandler(
            log_file, when="midnight", interval=1, backupCount=7, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # SQLite handler (90 dagen retentie)
        db_handler = SQLiteHandler(db_path, retention_days=90)
        db_handler.setFormatter(formatter)
        logger.addHandler(db_handler)

        # Filter toevoegen om class_name automatisch te loggen
        logger.addFilter(ClassNameFilter())

    return logger
