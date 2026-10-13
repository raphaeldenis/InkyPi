# Migration du Pi vers le schéma upstream

Cette procédure fait passer un Pi qui tourne sur le fork (ancien `device.json`, ancien service) à la version basée sur upstream. Elle est exécutée par une personne, jamais par un agent. Aucune valeur, aucun hôte et aucun lien n'y figure : chaque personne les saisit elle-même.

Repères utilisés ci-dessous :

- `<racine>` : la racine du dépôt déployé sur le Pi.
- `<horodatage>` : l'horodatage de la coupure, par exemple `20261014-0900`.
- `<sauvegarde>` : `/var/backups/inkypi/<horodatage>-pre-INK-024/`.

## Ce que la migration change

Le script `scripts/migrate_device_config.py` lit une **copie** du `device.json` et écrit un **autre** fichier. Il ne modifie jamais son entrée et ne lit jamais `.env`.

| Règle | Effet |
|---|---|
| `display_type` | ajouté avec la valeur passée à `--display-type` (obligatoire) ; une valeur déjà présente est conservée. Valeurs valides : `inky`, `mock`, ou un modèle Waveshare de la forme `epd…in…` |
| `inverted_image` | ajouté à `false` s'il manque |
| `plugin_cycle_interval_seconds` | posé à la valeur de `--cycle-interval-seconds` (1800 par défaut), jamais déduit de `scheduler_sleep_time` |
| Intervalles par instance | météo 3600 s, calendrier 10800 s, `image_upload` et `image_folder` 600 s |
| Instances de plugins retirés | toute instance dont le plugin n'a pas de `plugin-info.json` (`icalendar`, `icloud_calendar`) est supprimée de sa playlist ; ses noms sont affichés à la fin de l'exécution |
| `image_upload` | les clés du fork (minuterie, portrait, zoom, rotation, qualité, point focal) sont retirées ; `randomize` passe à `"true"` (sauf avec `--no-randomize`) |
| `weather` | `language` passe à `fr` quand la clé manque |
| `plugin_order` | laissé absent (ordre par défaut d'upstream) |
| Calendrier | aucune instance `calendar` n'est créée : le lien est saisi à la main (étape 6) |

Le script est idempotent : le relancer sur sa propre sortie ne change rien.

## Procédure

1. **Préflight.** Le playbook INK-025 est vert : Python 3.11 ou plus, glibc 2.34 ou plus, Chromium présent, disque et mémoire suffisants. Il confirme aussi la valeur de `display_type` de ce Pi.
2. **Début de la fenêtre de coupure.** Arrêter le service : `sudo systemctl stop inkypi`. L'écran garde sa dernière image jusqu'à l'étape 7.
3. **Sauvegardes, hors de l'arbre rsync.** Créer `<sauvegarde>` et y copier :
   - `<racine>/src/config/device.json`
   - `<racine>/.env`
   - `/usr/local/inkypi` (venv et CLI)
   - `/etc/systemd/system/inkypi.service`
   - `/usr/local/bin/inkypi`

   Écrire à côté la somme `sha256sum` de chaque fichier, puis la relire avec `sha256sum -c` avant de continuer. Ne jamais poser la sauvegarde sous `<racine>/src/` : le `rsync --delete` du déploiement l'effacerait.
4. **Migration de la configuration.** Depuis la sauvegarde, jamais en place :
   `python3 <racine>/scripts/migrate_device_config.py --in <sauvegarde>/device.json --out <racine>/src/config/device.json --display-type <valeur confirmée par INK-025>`

   Noter les noms d'instances retirés affichés par le script, puis vérifier que le fichier est du JSON valide : `python3 -m json.tool <racine>/src/config/device.json > /dev/null` (seul le code de sortie 0 compte, le contenu n'est pas affiché).
5. **Code.** Lancer `deploy.sh` (rsync du code), puis `install/update.sh` d'upstream (service, CLI, dépendances apt et pip). Ne pas lancer `install.sh` : il exécute `rm -rf /usr/local/inkypi` et détruit le venv. Si `update.sh` ne suffit pas sur un Pi encore au format du fork, `install.sh` n'est acceptable qu'avec l'étape 3 vérifiée, en sachant que le venv est recréé.
6. **Calendrier.** Dans l'interface web (réseau local uniquement), créer une instance du plugin `calendar`, coller le lien de partage dans `calendarURLs[]` et régler `language` sur `fr`. Aucune commande ne lit ni n'affiche ce lien. Ensuite, retirer `CALENDAR_URL` du `.env` : la page « API Keys » d'upstream lit et réécrit ce fichier. Contrôle : `grep -c '^CALENDAR_URL=' <racine>/.env` affiche `0`.
7. **Fin de la fenêtre de coupure.** `sudo systemctl start inkypi`, puis les contrôles :
   - `systemctl is-active inkypi` affiche `active` ;
   - `journalctl -u inkypi --since <heure du démarrage>` ne contient ni `Traceback` ni `OOM` ;
   - `journalctl -u inkypi --since <heure du démarrage> | grep -ci -E 'icloud|webcal'` affiche `0` ;
   - `systemctl show inkypi -p MemoryPeak` reste sous 200 Mo après un rendu du calendrier ;
   - sur trois cycles, la somme `sha256sum` de `current_image.png` change à chaque cycle de 30 minutes.
8. **Rollback.** À lancer si un contrôle de l'étape 7 échoue :
   1. `sudo systemctl stop inkypi` ;
   2. restaurer depuis `<sauvegarde>` les fichiers de l'étape 3, puis `sha256sum -c` sur chacun ;
   3. restaurer `src` et `install` depuis l'archive `pre-<sha>` créée par `deploy.sh` ;
   4. `sudo systemctl daemon-reload` ;
   5. `sudo systemctl start inkypi`, puis refaire les contrôles de l'étape 7.

## Fenêtre de coupure

L'écran n'est plus rafraîchi entre l'étape 2 et l'étape 7. Prévoir une durée de l'ordre de la demi-heure, dominée par `update.sh` (apt et pip sur un Pi Zero 2 W). `device.json` est réécrit par l'application à chaque rafraîchissement, sans écriture atomique : une coupure de courant pendant les étapes 4 à 7 sans la sauvegarde de l'étape 3 fait perdre la configuration.

## Essayer le script sur une copie

Le script s'essaie sur n'importe quelle copie jetable, sans toucher au Pi :

`python3 scripts/migrate_device_config.py --in <copie>.json --out <migré>.json --display-type inky`

Les tests de `tests/test_migrate_device_config.py` couvrent ces règles, l'idempotence, le démarrage de l'application sur la sortie migrée et l'absence d'écriture sur l'entrée.
