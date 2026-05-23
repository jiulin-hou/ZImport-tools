import sys
from zimport_tools.config import Config
from zimport_tools.web import create_app

cfg = Config(sys.argv[1] if len(sys.argv) > 1 else "/etc/zimport-tools/config.ini")
app = create_app(cfg)

if __name__ == "__main__":
    app.run(host=cfg.listen_host, port=cfg.listen_port)
