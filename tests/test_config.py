import textwrap
from zimport_tools.config import Config


def test_config_loads_all_fields(tmp_path):
    ini = tmp_path / "c.ini"
    ini.write_text(textwrap.dedent("""
        [server]
        listen_host = 127.0.0.1
        listen_port = 9000
        secret_key = abc
        [zimbra]
        soap_url = https://h/service/soap
        admin_soap_url = https://h:7071/service/admin/soap
        rest_base = https://h
        verify_tls = false
        [service_account]
        name = svc@d
        password = pw
        [storage]
        temp_root = /t
        db_path = /t/x.db
        max_task_bytes = 123
        retention_days = 5
        [scheduler]
        concurrency = 1
        queue_limit = 10
        [upload]
        chunk_size = 4096
    """))
    cfg = Config(str(ini))
    assert cfg.listen_port == 9000
    assert cfg.verify_tls is False
    assert cfg.svc_name == "svc@d"
    assert cfg.max_task_bytes == 123
    assert cfg.chunk_size == 4096
