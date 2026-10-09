# Public Demo Availability

The `https://raysource.cloud/demo/` route depends on three local processes:

1. `tools/run_server.py --port 8031 --with-model` serves the ICT8 application.
2. `tools/demo_prefix_proxy.py` serves `127.0.0.1:8032` and forwards `/demo/` to port 8031.
3. Cloudflare Tunnel publishes port 8032 using `runtime/public-tunnel/config.yml`.

Before this change, the Task Scheduler entries named `RaysourceWeb`, `RaysourceSiteMaintainer`, and `RaysourceRetrieval` pointed to legacy launchers under `D:\lanqun-site`; their latest runs failed on September 29. The ICT8 processes had been started manually, so a shell exit or process crash left no monitor to recover them.

`tools/install_public_demo_supervisor.ps1` registers `ICT8 Public Demo Watchdog` for the current Windows user at logon and starts it immediately. The watchdog checks local health, starts missing services, restarts an owned process that remains unhealthy for three minutes, and restarts its Cloudflare process after three consecutive failed public health checks when both local services are healthy. The scheduled task also restarts the watchdog if it exits. Logs are written under `runtime/public-tunnel/`.

The public URL still depends on this computer being powered on, awake, connected to the internet, and logged into the Windows account that runs the model-backed app. A PC in sleep, a router/ISP outage, a Cloudflare outage, or a credentials/provider outage cannot be repaired by a local watchdog. For continuous availability during sleep or power-off, deploy the app and tunnel on an always-on host.
