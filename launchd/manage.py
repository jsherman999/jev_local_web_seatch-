#!/usr/bin/env python3
"""Install two per-user LaunchAgents; paths are generated for this checkout."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = Path.home() / "Library" / "LaunchAgents"
DOMAIN = f"gui/{os.getuid()}"
LABELS = ["com.jay.jev.chrome", "com.jay.jev.api"]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def definitions():
    common = {
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "WorkingDirectory": str(ROOT),
        "ProcessType": "Background",
        "Umask": 63,
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
        },
    }
    commands = [
        [
            CHROME,
            "--headless=new",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9276",
            "--user-data-dir=" + str(ROOT / "data" / "chrome"),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "about:blank",
        ],
        [str(ROOT / ".venv/bin/python"), "-m", "jev_service"],
    ]
    return [
        {
            **common,
            "Label": label,
            "ProgramArguments": cmd,
            "StandardOutPath": str(ROOT / "logs" / (label + ".out.log")),
            "StandardErrorPath": str(ROOT / "logs" / (label + ".err.log")),
        }
        for label, cmd in zip(LABELS, commands)
    ]


def launch(*args, check=True):
    return subprocess.run(["launchctl", *args], check=check)


def stop():
    for label in reversed(LABELS):
        launch("bootout", f"{DOMAIN}/{label}", check=False)


def start():
    for label in LABELS:
        probe = subprocess.run(["launchctl", "print", f"{DOMAIN}/{label}"], capture_output=True)
        if probe.returncode:
            launch("bootstrap", DOMAIN, str(AGENTS / f"{label}.plist"))
        else:
            launch("kickstart", f"{DOMAIN}/{label}")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "install":
        if not Path(CHROME).exists() or not (ROOT / ".venv/bin/python").exists():
            raise SystemExit("Install Chrome and run uv sync first")
        AGENTS.mkdir(parents=True, exist_ok=True)
        for folder in ["data", "logs"]:
            (ROOT / folder).mkdir(exist_ok=True, mode=0o700)
        stop()
        for definition in definitions():
            path = AGENTS / (definition["Label"] + ".plist")
            path.write_bytes(plistlib.dumps(definition))
            path.chmod(0o644)
        start()
        print("Installed. Swagger: http://127.0.0.1:8776/docs")
    elif action == "start":
        start()
    elif action == "restart":
        # Leave Chrome running so its profile is not reopened while it is exiting.
        launch("kickstart", "-k", f"{DOMAIN}/com.jay.jev.api")
    elif action == "stop":
        stop()
    elif action == "status":
        for label in LABELS:
            result = subprocess.run(
                ["launchctl", "print", f"{DOMAIN}/{label}"], capture_output=True, text=True
            )
            print(label)
            print(
                "\n".join(
                    line.strip()
                    for line in result.stdout.splitlines()
                    if any(x in line for x in ("state =", "pid =", "last exit code ="))
                )
                if result.returncode == 0
                else "not loaded"
            )
    elif action == "logs":
        for p in sorted((ROOT / "logs").glob("*.log")):
            print(p.name)
            print("\n".join(p.read_text(errors="replace").splitlines()[-40:]))
    elif action == "uninstall":
        stop()
        for label in LABELS:
            (AGENTS / f"{label}.plist").unlink(missing_ok=True)
        print("LaunchAgents removed. Project files and data retained.")
    else:
        raise SystemExit("Usage: control.sh {install|start|restart|stop|status|logs|uninstall}")


if __name__ == "__main__":
    main()
