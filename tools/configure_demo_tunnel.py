"""Create a private runtime copy of the existing tunnel config for /demo."""
from __future__ import annotations

from pathlib import Path
import yaml


SOURCE = Path(r"D:\lanqun-site\cloudflared\config.yml")
TARGET = Path(__file__).resolve().parents[1] / "runtime/public-tunnel/config.yml"


def main() -> None:
    config = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    ingress = config.get("ingress")
    if not isinstance(ingress, list):
        raise SystemExit("Tunnel ingress is not a list")
    if any(rule.get("hostname") == "raysource.cloud" and rule.get("path", "").startswith("^/demo")
           for rule in ingress):
        raise SystemExit("A /demo ingress already exists in the generated config")
    index = next((i for i, rule in enumerate(ingress)
                  if rule.get("hostname") == "raysource.cloud" and not rule.get("path")), None)
    if index is None:
        raise SystemExit("Could not locate raysource.cloud fallback ingress")

    credentials = config.get("credentials-file")
    if credentials:
        credential_path = Path(credentials)
        if not credential_path.is_absolute():
            credential_path = SOURCE.parent / credential_path
        if not credential_path.is_file():
            raise SystemExit("Configured tunnel credential file is missing")
        config["credentials-file"] = str(credential_path.resolve())

    ingress[index:index] = [
        {"hostname": "raysource.cloud", "path": "^/demo(/.*)?$",
         "service": "http://127.0.0.1:8032"},
        {"hostname": "raysource.cloud", "path": "^/health$",
         "service": "http://127.0.0.1:8032"},
        {"hostname": "raysource.cloud",
         "path": "^/api/v1/(omni|knowledge|nl2sql|data-sources|documents)(/.*)?$",
         "service": "http://127.0.0.1:8032"},
    ]
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print("Created private tunnel config with /demo and ICT8 API routes")


if __name__ == "__main__":
    main()
