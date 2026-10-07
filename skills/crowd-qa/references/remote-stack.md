# Running the backend on another machine

Use it when the machine running the simulator is short on RAM, cores or disk. The other machine runs the
whole backend; this one keeps only the orchestration, the testers and any devices.

## Access
1. Both machines on one network: `ping` both ways. If ARP for the address stays "incomplete", no firewall
   rule will help. They are on different networks, or the router isolates Wi-Fi clients.
2. A dedicated key: `ssh-keygen -t ed25519 -N "" -f ~/.ssh/crowd_qa_ed25519`. The owner installs it once
   with `ssh-copy-id -i ~/.ssh/crowd_qa_ed25519.pub <user>@<host>` (they type their password; it never goes
   into a file or the chat).
3. An alias in `~/.ssh/config` (`Host stack-host`, HostName, User, IdentityFile, `BatchMode yes`), so
   testers and scripts use `ssh stack-host`.

## On the remote
- Survey first, read-only: cores, RAM, disk, the backend checkout and its commit, env files (print names,
  never values), running containers, listening ports, sudo, Docker group. Report before changing anything.
- Leave the owner's things alone. Your containers get a `crowd-` prefix and their own ports
  (e.g. Postgres 54336, Redis 6380, MinIO 9111), volumes, and `restart unless-stopped`.
- Put every setting in a launcher (`~/crowd-qa/api.sh`) that exports them and execs the server. Do not edit
  the checkout or its env files. The backend's dotenv still loads its own `.env*` for anything you do not
  export, so export **empty** values for anything dangerous you must keep out (a production read-only URL,
  live payment keys).
- Read the env schema for how flags parse. In one backend, `flag()` accepted only `true`, so `1` silently
  meant off.
- Generate keys that must survive restarts (credential encryption) once, into a `chmod 600` file the launcher reads.
- Without sudo, a member of the docker group can still add a loopback address:
  `docker run --rm --net=host --cap-add=NET_ADMIN --entrypoint ip <any alpine image> addr add 203.0.113.1/32 dev lo`.
  It is lost on reboot, so `remote-up.sh` re-adds it.
- Kill by pid file, never `pkill -f <script name>` over SSH: the pattern matches your own SSH command and ends it.
- Detach long-running servers with `setsid nohup … < /dev/null &` so the SSH session can close.
- Write `~/crowd-qa/remote-up.sh`: start containers, re-add the loopback address, start the API if it is
  not answering. One command after the owner reboots.

## On this machine
- `scripts/tunnel.sh` forwards each `TUNNEL_PORTS` port from `localhost` here to `localhost` there, with the
  **same** port numbers. An app built for `localhost:<port>` then works unchanged, and the Host header the
  backend sees (`localhost:<port>`) is one its own allowed-hosts list already accepts. Uploads work too,
  if presigned URLs are built on `localhost:<storage port>`.
- Docker stays off here. `context.md` tells testers how to read codes and logs over `ssh stack-host`.
- `net-stall.sh` freezes the tunnel's ssh for a weak-signal character (`STACK_MODE=remote`).
- `preflight.sh` checks the tunnel ports and the remote shell, then runs the stack check through the tunnel.
