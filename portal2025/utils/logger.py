import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta


class SQLiteHandler(logging.Handler):
    def __init__(self, db_path="logs/app_logs.db", retention_days=7):
        super().__init__()
        self.db_path = db_path
        self.retention_days = retention_days
        self._initialize_db()

    def _initialize_db(self):
        """Initialiseert de SQLite database en maakt de logs-tabel als deze nog niet bestaat."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
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
        finally:
            conn.close()

    def emit(self, record):
        """Schrijft een logrecord naar de SQLite database."""
        log_time = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
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
        finally:
            conn.close()

        self.cleanup_old_logs()

    def cleanup_old_logs(self):
        """Verwijdert logrecords die ouder zijn dan de ingestelde retentieperiode."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        cutoff_str = cutoff_date.isoformat()

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM logs WHERE timestamp < ?", (cutoff_str,))
            conn.commit()
        finally:
            conn.close()


def get_logger(name, log_file="logs/app.log", db_path="logs/app_logs.db"):
    """Configureert een logger die logt naar console (DEBUG), bestand en SQLite (vanaf WARNING)."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Laat alle niveaus door, handlers bepalen wat ze verwerken

    # Console handler (DEBUG en hoger)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    # File handler (alleen WARNING en hoger)
    if not os.path.exists(os.path.dirname(log_file)):
        os.makedirs(os.path.dirname(log_file))
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.WARNING)

    # SQLite handler (alleen WARNING en hoger)
    sh = SQLiteHandler(db_path=db_path)
    sh.setLevel(logging.WARNING)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - [%(filename)s:%(lineno)d] - %(funcName)s - %(message)s')

    ch.setFormatter(formatter)
    fh.setFormatter(formatter)

    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.addHandler(sh)

    return logger
