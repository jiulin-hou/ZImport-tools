import configparser


class Config:
    def __init__(self, path):
        cp = configparser.ConfigParser()
        if not cp.read(path):
            raise FileNotFoundError("config not found: %s" % path)
        self.listen_host = cp.get("server", "listen_host", fallback="127.0.0.1")
        self.listen_port = cp.getint("server", "listen_port", fallback=8088)
        self.secret_key = cp.get("server", "secret_key")
        self.soap_url = cp.get("zimbra", "soap_url")
        self.admin_soap_url = cp.get("zimbra", "admin_soap_url")
        self.rest_base = cp.get("zimbra", "rest_base").rstrip("/")
        self.verify_tls = cp.getboolean("zimbra", "verify_tls", fallback=True)
        self.svc_name = cp.get("service_account", "name")
        self.svc_password = cp.get("service_account", "password")
        self.temp_root = cp.get("storage", "temp_root")
        self.db_path = cp.get("storage", "db_path")
        self.max_task_bytes = cp.getint("storage", "max_task_bytes",
                                        fallback=10 * 1024 ** 3)
        self.retention_days = cp.getint("storage", "retention_days", fallback=7)
        self.concurrency = cp.getint("scheduler", "concurrency", fallback=1)
        self.queue_limit = cp.getint("scheduler", "queue_limit", fallback=50)
        self.dedupe = cp.getboolean("scheduler", "dedupe", fallback=True)
        self.chunk_size = cp.getint("upload", "chunk_size", fallback=10 * 1024 * 1024)
