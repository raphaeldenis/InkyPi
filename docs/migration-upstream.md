# Migration du Pi vers le schéma upstream

Cette procédure fait passer un Pi qui tourne sur le fork (ancien `device.json`, ancien service) à la version basée sur upstream. Elle est exécutée par une personne, jamais par un agent. Aucune valeur, aucun hôte et aucun lien n'y figure : chaque personne les saisit elle-même.

Repères utilisés ci-dessous :

- `<racine>` : la racine du dépôt déployé sur le Pi.
- `<horodatage>` : l'horodatage de la coupure, par exemple `20261014-0900`.
- `<sauvegarde>` : `/var/backups/inkypi/<horodatage>-pre-INK-024`, hors de l'arbre `rsync`.
- `<checkout upstream>` : le checkout du dépôt upstream sur le poste, celui qui contient les `plugin-info.json`.
- `<migré>` : le fichier `device.json` migré, produit sur le poste.

## Ce que la migration change

Le script `scripts/migrate_device_config.py` lit une **copie** du `device.json` et écrit un **autre** fichier, de façon atomique. Il ne modifie jamais son entrée et ne lit jamais `.env`.

| Règle | Effet |
|---|---|
| `display_type` | ajouté avec la valeur passée à `--display-type` (obligatoire) ; une valeur déjà présente est conservée |
| `inverted_image` | ajouté à `false` s'il manque ; une valeur existante est conservée |
| `plugin_cycle_interval_seconds` | posé à la valeur de `--cycle-interval-seconds` (1800 par défaut, entier de 1 à 86400), jamais déduit de `scheduler_sleep_time` |
| Intervalles par instance | météo 3600 s, calendrier 10800 s, `image_upload` et `image_folder` 600 s ; une instance qui a une heure `scheduled` ne reçoit pas d'intervalle et garde son `refresh` tel quel (upstream cumule les deux déclencheurs) |
| Instances de plugins retirés | toute instance dont le plugin n'a pas de `plugin-info.json` dans `--plugins-dir` (`icalendar`, `icloud_calendar`) est supprimée de sa playlist |
| `image_upload` | les clés du fork (minuterie, portrait, zoom, rotation, qualité, point focal) sont retirées ; `randomize` passe à `"true"` (sauf avec `--no-randomize`) |
| `weather` | `language` passe à `fr` quand la clé manque, uniquement sur la météo |
| `plugin_order` | laissé absent (ordre par défaut d'upstream) |
| Calendrier | aucune instance `calendar` n'est créée : le lien est saisi à la main (étape 6) |

Le script refuse de travailler avec le code de sortie 2 et un message sans aucun contenu de configuration ; `--help` détaille les options et les refus. À la fin, il affiche les noms des instances retirées puis `kept N / removed M`, jamais un réglage.

Le script est idempotent : le relancer sur sa propre sortie ne change rien.

## Procédure

La fenêtre de coupure va de l'étape 2 à la fin de l'étape 5 : `install/update.sh` redémarre le service, il n'y a donc pas d'étape de démarrage séparée.

1. **Préflight.** Le playbook INK-025 est vert : Python 3.11 ou plus, glibc 2.34 ou plus, Chromium présent, disque et mémoire suffisants. Il confirme aussi la valeur de `display_type` de ce Pi.
2. **Arrêt du service.** `sudo systemctl stop inkypi`. L'écran garde sa dernière image jusqu'au redémarrage de l'étape 5.
3. **Sauvegardes, hors de l'arbre `rsync`.** Ne jamais poser la sauvegarde sous `<racine>/src/` : le `rsync --delete` du déploiement l'effacerait.
   1. Créer le dossier avec des droits stricts : `umask 077`, puis `mkdir -p <sauvegarde>` et `chmod 700 <sauvegarde>`.
   2. Archiver les répertoires : `tar -cf <sauvegarde>/inkypi-dirs.tar -C / usr/local/inkypi`, puis `sha256sum <sauvegarde>/inkypi-dirs.tar > <sauvegarde>/inkypi-dirs.tar.sha256`. Ne jamais calculer `sha256sum` sur un répertoire.
   3. Copier les fichiers : `<racine>/src/config/device.json`, `<racine>/.env`, `/etc/systemd/system/inkypi.service` et `/usr/local/bin/inkypi` vers `<sauvegarde>`, puis écrire à côté la somme `sha256sum` de chaque fichier copié.
   4. Relire toutes les sommes avec `sha256sum -c` avant de continuer.
4. **Migration sur le poste.** Le script n'est pas déployé sur le Pi (le `rsync` ne transporte que `src` et `install`) ; la migration se fait sur le poste de Raphaël.
   1. Rapatrier sur le poste la sauvegarde de `device.json` (copie, jamais l'original du Pi).
   2. Lancer : `python3 scripts/migrate_device_config.py --in <copie rapatriée> --out <migré> --display-type <valeur confirmée par INK-025> --plugins-dir <checkout upstream>/src/plugins`. Le dossier passé à `--plugins-dir` est celui d'**upstream**, qui contient les `plugin-info.json`, jamais celui de l'ancien Pi.
   3. Vérifier le rapport : il ne doit lister que des instances calendrier (`icalendar`, `icloud_calendar`) ; tout autre nom retiré est une anomalie à comprendre avant de continuer. Le compte `kept N / removed M` doit correspondre à ce que Raphaël attend.
   4. Vérifier que `<migré>` est du JSON valide : `python3 -m json.tool <migré> > /dev/null` (seul le code de sortie 0 compte, le contenu n'est pas affiché).
   5. Pendant la coupure, recopier `<migré>` sur le Pi vers `<racine>/src/config/device.json`.
5. **Code, puis `install/update.sh`.** Lancer `deploy.sh` (`rsync` du code), puis `install/update.sh` d'upstream (service, CLI, dépendances apt et pip). `update.sh` exécute `systemctl restart` : le service redémarre ici, c'est la fin de la fenêtre de coupure. Ne pas lancer `install.sh` : il exécute `rm -rf /usr/local/inkypi` et détruit le venv. Si `update.sh` ne suffit pas sur un Pi encore au format du fork, `install.sh` n'est acceptable qu'avec l'étape 3 vérifiée, en sachant que le venv est recréé.
6. **Calendrier.** L'interface web exige le service démarré (étape 5 terminée). Dans l'interface web (réseau local uniquement), Raphaël crée une instance du plugin `calendar`, colle le lien de partage (jamais affiché ni recopié ailleurs) dans `calendarURLs[]` et règle `language` sur `fr`. Ensuite, il retire `CALENDAR_URL` du `.env` : la page « API Keys » d'upstream lit et réécrit ce fichier. Contrôle : `grep -c '^CALENDAR_URL=' <racine>/.env` affiche `0`.
7. **Contrôles.**
   - `systemctl is-active inkypi` affiche `active` ;
   - `journalctl -u inkypi --since <heure du redémarrage>` ne contient ni `Traceback` ni `OOM` ;
   - `journalctl -u inkypi --since <heure du redémarrage> | grep -ci -E 'icloud|webcal'` affiche `0` ;
   - `systemctl show inkypi -p MemoryPeak` reste sous 200 Mo après un rendu du calendrier ;
   - sur trois cycles, la somme `sha256sum` de `current_image.png` change à chaque cycle de 30 minutes.
8. **Rollback.** À lancer si un contrôle de l'étape 7 échoue :
   1. `sudo systemctl stop inkypi` ;
   2. restaurer depuis `<sauvegarde>` : désarchiver `inkypi-dirs.tar`, puis remettre `device.json`, `inkypi.service` et `inkypi` à leur place ; contrôler chaque fichier avec `sha256sum -c` ;
   3. restaurer **explicitement** l'ancien `.env` depuis `<sauvegarde>` avant tout redémarrage (l'étape 6 l'a modifié), puis `sha256sum -c` sur lui ;
   4. restaurer `src` et `install` depuis l'archive `pre-<sha>` créée par `deploy.sh` ;
   5. `sudo systemctl daemon-reload` ;
   6. `sudo systemctl start inkypi`, puis refaire les contrôles de l'étape 7.

## Fenêtre de coupure

L'écran n'est plus rafraîchi pendant cette fenêtre. Prévoir une durée de l'ordre de la demi-heure, dominée par `update.sh` (apt et pip sur un Pi Zero 2 W). `device.json` est réécrit par l'application à chaque rafraîchissement, sans écriture atomique : une coupure de courant pendant les étapes 3 à 5 sans la sauvegarde de l'étape 3 fait perdre la configuration.
