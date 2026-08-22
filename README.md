# Magnet2Torrent

A lightweight PyQt5 desktop app for Linux (built for Debian/KDE) that converts magnet
links into real `.torrent` files and uploads them straight to a directory on a remote
server over SSH/SCP.

No browser extensions, no third-party web services — everything runs locally and talks
directly to your own server.

## Features

- **Magnet → torrent** — fetches full metadata from the BitTorrent DHT/peer swarm
  and builds a proper `.torrent` file (not just a metadata stub).
- **One-click upload** — sends the generated `.torrent` straight to a directory on
  your remote server via `scp`.
- **Multiple server profiles** — save as many host/user/port/directory combos as
  you need and switch between them from a dropdown.
- **SSH key or password auth** — works with passwordless key-based login out of the
  box, or password auth via `sshpass` if you'd rather not set up keys.
- **Native Qt GUI** — fits right into KDE Plasma, no Electron/browser overhead.
- **Load from file** — paste a magnet link directly, or point it at a `.magnet`
  file on disk.

## Screenshots

*(add your own screenshots here, e.g. `docs/screenshot-convert.png` and
`docs/screenshot-servers.png`)*

## Requirements

- Linux with Python 3 and Qt5 (tested on Debian + KDE Plasma)
- An SSH server you can already reach — either with a passwordless key set up
  (`ssh-copy-id`) or a username/password

## Installation

Clone the repo:

```bash
git clone https://github.com/<your-username>/magnet2torrent.git
cd magnet2torrent
```

Install the dependencies from Debian's repos:

```bash
sudo apt update
sudo apt install python3-pyqt5 python3-libtorrent openssh-client sshpass
```

> `sshpass` is only required if you plan to use password authentication for a server
> profile. If you're using SSH keys exclusively, you can skip it.

## Usage

Run the app:

```bash
python3 magnet2torrent.py
```

### 1. Add a server profile

Open the **Servers** tab and click **Add...**:

| Field | Description |
|---|---|
| Profile name | A label for yourself, e.g. `Home NAS` |
| Host / IP | The server's hostname or IP address |
| SSH port | Defaults to `22` |
| Username | The SSH user to connect as |
| Target directory | Where `.torrent` files should land on the server |
| Authentication | `SSH key (passwordless)` or `Password` |

If you choose **Password**, you can optionally tick **Remember password** to save it
for next time, or leave it unchecked to be prompted each time instead.

### 2. Convert & send

Switch to the **Convert & Send** tab:

1. Paste a magnet link, or click **Load from .magnet file...**
2. Pick a server profile from the dropdown
3. *(Optional)* Click **Test connection** to confirm SSH access works
4. Click **Convert & Send**

The app will fetch the torrent's metadata from the DHT/peer swarm, build a
`.torrent` file, and upload it to your server's target directory — all in the
background, without freezing the UI.

## How it works

Magnet links only contain an info-hash and tracker/peer hints — not the full file
list and piece hashes that make up a `.torrent` file. To do the conversion, the app
uses [libtorrent](https://www.libtorrent.org/) to briefly join the swarm (via DHT
and/or trackers), pull down just the metadata, and write it out as a standard
`.torrent` file. This means:

- The magnet link needs to have active peers/seeds available for the conversion to
  succeed.
- Conversion can take anywhere from a few seconds to about a minute, and will time
  out after 120 seconds by default (configurable in `magnet2torrent.py`).

## Configuration & data storage

Server profiles are stored locally at:

```
~/.config/magnet2torrent/servers.json
```

The file permissions are set to `600` (owner-only). If you enable **Remember
password** for a profile, note that the password is stored in **plain text** in this
file — fine for a personal machine, but be aware of it if the machine is shared.
Leave it unchecked to be prompted each time instead. SSH-key profiles never store a
password.

## Troubleshooting

- **"python3-libtorrent is not installed"** — `sudo apt install python3-libtorrent`
- **"scp not found" / "sshpass not found"** — `sudo apt install openssh-client sshpass`
- **Metadata fetch times out** — the magnet link may have no active seeds/peers, or
  your network/firewall is blocking DHT traffic
- **SSH connection fails** — use the **Test connection** button to see the exact
  SSH error; for key auth, confirm `ssh user@host` works from a terminal first

## Contributing

Issues and pull requests are welcome. If you're proposing a larger change (e.g. a
new transfer protocol or a system-tray mode), please open an issue first to discuss.

## License

GPL v3 — feel free to fork, share and adapt.
