# Datum

Local site for Datum, an embedded product studio. The work runs from concept design to hardware ready for mass production: mechanical, electrical, and firmware.

## Service

The page is a user service, `datum-site.service`, on http://127.0.0.1:4173/

```bash
systemctl --user status datum-site.service
systemctl --user restart datum-site.service
```

Logs: `journalctl --user -u datum-site.service -e`

The unit file is `~/.config/systemd/user/datum-site.service`.

The studio name, the case studies, and `hello@datum.studio` live in `index.html` and `js/main.js`.
