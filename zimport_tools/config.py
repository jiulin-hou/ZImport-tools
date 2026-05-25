import configparser


class Config:
    def __init__(self, path):
        cp = configparser.ConfigParser()
        if not cp.read(path):
            raise FileNotFoundError("config not found: %s" % path)
        self.listen_host = cp.get("server", "listen_host", fallback="127.0.0.1")
        self.listen_port = cp.getint("server", "listen_port", fallback=8088)
        self.soap_url = cp.get("zimbra", "soap_url")
        self.admin_soap_url = cp.get("zimbra", "admin_soap_url")
        self.rest_base = cp.get("zimbra", "rest_base").rstrip("/")
        self.verify_tls = cp.getboolean("zimbra", "verify_tls", fallback=True)
        # Optional path to a CA bundle to trust (e.g. Zimbra's own
        # /opt/zimbra/conf/ca/ca.pem on same-host installs). When set,
        # tls_verify() returns this path so requests does real CA-chain
        # verification while still trusting the self-signed cert.
        self.ca_bundle = cp.get("zimbra", "ca_bundle", fallback="").strip()
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

    def tls_verify(self):
        """Value to pass as `requests.*(verify=...)`. A CA bundle path
        wins over the boolean toggle."""
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_tls
