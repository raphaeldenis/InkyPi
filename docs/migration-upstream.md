# Migration du Pi vers le schéma upstream

Cette procédure fait passer un Pi qui tourne sur le fork (ancien `device.json`, ancien service) à la version basée sur upstream. Elle est exécutée par une personne, jamais par un agent. Aucune valeur, aucun hôte et aucun lien n'y figure : chaque personne les saisit elle-même.

Repères utilisés ci-dessous :

- `<racine>` : la racine du dépôt déployé sur le Pi.
- `<horodatage>` : l'horodatage de la coupure, par exemple `20261014-0900`.
- `<sauvegarde>` : `/var/backups/inkypi/<horodatage>-pre-INK-024`, hors de l'arbre `rsync`.
- `<checkout upstream>` : le checkout du dépôt upstream sur le poste, celui qui contient les `plugin-info.json`.
- `<migré>` : le fichier `device.json` migré, produit sur le poste.

## Prérequis bloquants

Ne pas lancer la procédure tant qu'un de ces trois points manque :

1. **INK-027 est fait.** Cette ligne du backlog adapte `deploy.sh` (hors dépôt) : mode « première migration » sans `restart` ni `pip` prématuré avant `install/update.sh`, `tar` avec `umask 077`, rollback complet, `.deployed-sha` remis au sha précédent. Le `deploy.sh` actuel redémarre le service avant `update.sh` : l'ancien lanceur, l'ancien service et l'ancienne CLI tourneraient avec le nouvel arbre `src`, et la fenêtre de coupure annoncée ne tiendrait pas.
2. **La PR L3 (météo FR) est fusionnée et déployée avant ce lot, ou avec lui.** `language=fr` n'a d'effet qu'avec L3 : sans elle, la météo reste en anglais malgré la migration.
3. **Le playbook INK-025 est vert** (étape 1).

## Ce que la migration change

Le script `scripts/migrate_device_config.py` lit une **copie** du `device.json` et écrit un **autre** fichier, de façon atomique. Il ne modifie jamais son entrée et ne lit jamais `.env`.

| Règle | Effet |
|---|---|
| `display_type` | ajouté avec la valeur passée à `--display-type` (obligatoire) ; une valeur déjà présente est conservée. La valeur **effective** (celle qui reste dans le fichier migré) doit être `inky`, `mock` ou un modèle Waveshare `epd…in…` : `null`, une liste, un nombre ou toute autre chaîne font refuser la migration, sans citer la valeur |
| `inverted_image` | ajouté à `false` s'il manque ; une valeur existante est conservée |
| `plugin_cycle_interval_seconds` | posé à la valeur de `--cycle-interval-seconds` (1800 par défaut, entier de 1 à 86400), jamais déduit de `scheduler_sleep_time` |
| Intervalles par instance | météo 3600 s, calendrier 10800 s, `image_upload` et `image_folder` 600 s. Une instance qui a une heure `scheduled` **ne reçoit pas d'intervalle** (upstream cumule les deux déclencheurs) ; si elle a déjà un `interval`, elle le garde tel quel, avec son `scheduled` |
| Plugins absents de l'inventaire | une instance dont le `plugin_id` n'a pas de `plugin-info.json` dans `--plugins-dir` est supprimée **seulement** si ce `plugin_id` est autorisé à la suppression : `icalendar` et `icloud_calendar` par défaut, plus chaque `--remove-plugin-id <id>` (option répétable). Tout autre `plugin_id` absent fait refuser la migration (code 2, le message nomme le `plugin_id`, jamais un réglage) |
| `current_plugin_index` | suit les suppressions : la prochaine instance affichée par la playlist reste celle qui l'aurait été avant la migration. Un index hors bornes est lu comme upstream le lit (modulo la taille de la playlist) ; `null` reste `null` ; une playlist vidée repasse à `null` ; une valeur qui n'est ni un entier ni `null` fait refuser la migration |
| `image_upload` | les clés du fork (minuterie, portrait, zoom, rotation, qualité, point focal) sont retirées ; `randomize` passe à `"true"` (sauf avec `--no-randomize`) |
| `weather` | `language` passe à `fr` quand la clé manque, uniquement sur la météo |
| `plugin_order` | laissé absent (ordre par défaut d'upstream) |
| Calendrier | aucune instance `calendar` n'est créée : le lien est saisi à la main (étape 6) |

Le script refuse de travailler avec le code de sortie 2 et un message sans aucun contenu de configuration ; `--help` détaille les options et les refus. À la fin, il affiche une ligne par instance retirée (`playlist`, `plugin_id` et nom, tous trois échappés : ni caractère de contrôle ni retour à la ligne) puis `kept N / removed M`, jamais un réglage.

Limite connue : les modèles Waveshare ne sont validés que par le motif `epd*in*`, pas par la présence du pilote correspondant. Un modèle bien écrit mais absent de la bibliothèque d'écran passe la migration et échoue au démarrage du service ; INK-025 confirme le modèle réel.

Le script est idempotent : le relancer sur sa propre sortie ne change rien.

## Procédure

La fenêtre de coupure va de l'étape 2 à la fin de l'étape 5 : `install/update.sh` redémarre le service, il n'y a donc pas d'étape de démarrage séparée.

1. **Préflight.** Le playbook INK-025 est vert : Python 3.11 ou plus, glibc 2.34 ou plus, Chromium présent, disque et mémoire suffisants. Il confirme aussi la valeur de `display_type` de ce Pi.
2. **Décision ops sur le rollback.** Lire la section « Rollback » : il ne restaure ni les paquets `apt` ni les services ajoutés par `update.sh`. Décider et consigner avant la coupure : accepter ces restes, ou prendre une image de la carte SD (seul retour complet).
3. **Arrêt du service.** `sudo systemctl stop inkypi`. L'écran garde sa dernière image jusqu'au redémarrage de l'étape 5.
4. **Sauvegardes, hors de l'arbre `rsync`, AVANT toute migration.** Cette sauvegarde est le seul instantané de l'ancienne configuration : l'archive que `deploy.sh` prend plus tard est faite après la copie du nouveau `device.json` et ne la contient pas (voir « Rollback »). Ne jamais poser la sauvegarde sous `<racine>/src/` : le `rsync --delete` du déploiement l'effacerait.
   1. Créer le dossier avec des droits stricts : `umask 077`, puis `mkdir -p <sauvegarde>` et `chmod 700 <sauvegarde>`.
   2. Archiver les répertoires : `tar -cf <sauvegarde>/inkypi-dirs.tar -C / usr/local/inkypi`, puis `sha256sum <sauvegarde>/inkypi-dirs.tar > <sauvegarde>/inkypi-dirs.tar.sha256`. Ne jamais calculer `sha256sum` sur un répertoire.
   3. Copier les fichiers : `<racine>/src/config/device.json`, `<racine>/.env`, `<racine>/.deployed-sha` (s'il existe), `/etc/systemd/system/inkypi.service` et `/usr/local/bin/inkypi` vers `<sauvegarde>`, puis écrire à côté la somme `sha256sum` de chaque fichier copié. Le rollback complet s'appuie sur `.deployed-sha` pour remettre le sha précédent.
   4. Écrire la somme des **emplacements d'origine** (le service est arrêté, ils ne bougent plus) : `sha256sum <racine>/src/config/device.json <racine>/.env /etc/systemd/system/inkypi.service /usr/local/bin/inkypi > <sauvegarde>/destinations.sha256`. Le rollback s'en sert pour contrôler les destinations. `destinations.sha256` sert de contrôle de fraîcheur avant la première migration pour `.env`, le service et la CLI uniquement ; `device.json` en est exclu volontairement (l'étape 5.5 le remplace par la version migrée avant `deploy.sh`) et doit seulement être présent sur le Pi, régulier et non vide. Le contrôle de sortie du rollback complet (étape 7 du Rollback) porte, lui, sur les quatre destinations.
   5. Relire toutes les sommes avec `sha256sum -c` avant de continuer.
5. **Migration sur le poste.** Le script n'est pas déployé sur le Pi (le `rsync` ne transporte que `src` et `install`) ; la migration se fait sur le poste de Raphaël.
   1. Rapatrier sur le poste la sauvegarde de `device.json` (copie, jamais l'original du Pi).
   2. Lancer : `python3 scripts/migrate_device_config.py --in <copie rapatriée> --out <migré> --display-type <valeur confirmée par INK-025> --plugins-dir <checkout upstream>/src/plugins`. Le dossier passé à `--plugins-dir` est celui d'**upstream**, qui contient les `plugin-info.json`, jamais celui de l'ancien Pi.
   3. Vérifier le rapport : il ne doit lister que des instances calendrier (`icalendar`, `icloud_calendar`). Un autre `plugin_id` absent de l'inventaire fait refuser la migration : comprendre pourquoi avant d'ajouter `--remove-plugin-id <id>`. Le compte `kept N / removed M` doit correspondre à ce que Raphaël attend.
   4. Vérifier que `<migré>` est du JSON valide : `python3 -m json.tool <migré> > /dev/null` (seul le code de sortie 0 compte, le contenu n'est pas affiché).
   5. Pendant la coupure, recopier `<migré>` sur le Pi vers `<racine>/src/config/device.json`. Cette recopie a lieu AVANT `deploy.sh --first-upstream-migration` (étape 6) et constitue le dernier contrôle de la fenêtre avant le déploiement. Le playbook `migrate-pi-to-upstream` enchaîne les cellules de cette procédure.
6. **Code, puis `install/update.sh`.** Lancer `deploy.sh` en mode « première migration » d'INK-027 (`rsync` du code, sans redémarrage), puis `install/update.sh` d'upstream (service, CLI, dépendances apt et pip). `update.sh` exécute `systemctl restart` : le service redémarre ici, c'est la fin de la fenêtre de coupure. Ne pas lancer `install.sh` : il exécute `rm -rf /usr/local/inkypi` et détruit le venv. Si `update.sh` ne suffit pas sur un Pi encore au format du fork, `install.sh` n'est acceptable qu'avec l'étape 4 vérifiée, en sachant que le venv est recréé.
7. **Calendrier.** L'interface web exige le service démarré (étape 6 terminée). Dans l'interface web (réseau local uniquement), Raphaël crée une instance du plugin `calendar`, colle le lien de partage (jamais affiché ni recopié ailleurs) dans `calendarURLs[]` et règle `language` sur `fr`. Ensuite, il retire `CALENDAR_URL` du `.env` : la page « API Keys » d'upstream lit et réécrit ce fichier. Contrôle : `grep -c '^CALENDAR_URL=' <racine>/.env` affiche `0`.
8. **Contrôles.**
   - `systemctl is-active inkypi` affiche `active` ;
   - `journalctl -u inkypi --since <heure du redémarrage>` ne contient ni `Traceback` ni `OOM` ;
   - `journalctl -u inkypi --since <heure du redémarrage> | grep -ci -E 'icloud|webcal'` affiche `0` ;
   - `systemctl show inkypi -p MemoryPeak` reste sous 200 Mo après un rendu du calendrier ;
   - le journal montre les rafraîchissements aux échéances attendues, sur au moins trois cycles de 30 minutes : une ligne `Updating display.` par cycle ; la ligne `Refreshing plugin instance. | plugin_instance: '<nom>'` à chaque échéance d'intervalle de l'instance (météo toutes les heures, calendrier toutes les trois heures, photos toutes les dix minutes, donc à chaque cycle) ; entre deux échéances, `Not time to refresh plugin instance, using latest image.` est normal. Une ligne `Image already displayed, skipping refresh.` est normale aussi : le tirage des photos se fait avec remise et une même photo peut revenir.

   Le hash de `current_image.png` n'est pas un oracle : il peut rester identique à un cycle sans qu'il y ait de défaut (photo tirée deux fois, image en cache).
9. **Rollback.** Voir la section suivante ; à lancer si un contrôle de l'étape 8 échoue.

## Rollback

**Le rollback n'est pas un retour complet.** Il remet le code, le venv, le service, la CLI, `device.json` et `.env` dans l'état de l'étape 4 ; il laisse en place ce que l'étape 6 a installé hors de ces fichiers (liste plus bas).

Ordre imposé : **`device.json` est restauré en dernier**, après toutes les extractions d'archives. Raison : l'archive `pre-<sha>` de `deploy.sh` est prise **après** la copie du nouveau `device.json` (étape 5.5) ; elle contient donc la configuration **migrée**. Extraire cette archive après avoir restauré l'ancien `device.json` écrase la restauration. La seule source de l'ancienne configuration est la sauvegarde de l'étape 4, faite avant la migration.

1. `sudo systemctl stop inkypi`.
2. Contrôler la sauvegarde : `sha256sum -c` sur `inkypi-dirs.tar.sha256` et sur les sommes des fichiers copiés.
3. **Remplacer l'arbre `/usr/local/inkypi`, sans superposition.** Désarchiver par-dessus l'arbre actuel ne restaure pas le venv : un paquet ajouté par `pip` reste en place.
   1. Déplacer l'arbre actuel de côté : `sudo mv /usr/local/inkypi /usr/local/inkypi.set-aside`.
   2. Extraire l'archive vérifiée : `sudo tar -xf <sauvegarde>/inkypi-dirs.tar -C /`.
   3. Tester l'arbre restauré : `sudo tar -df <sauvegarde>/inkypi-dirs.tar -C /` ne signale aucun écart (hors fichiers `.pyc`), et le Python du venv restauré démarre.
   4. Seulement alors, supprimer la copie de côté : `sudo rm -rf /usr/local/inkypi.set-aside`. En cas d'échec de l'extraction ou du test, supprimer l'arbre partiel et remettre la copie de côté à sa place.
4. Restaurer le service et la CLI depuis `<sauvegarde>` : `/etc/systemd/system/inkypi.service` et `/usr/local/bin/inkypi`.
5. Restaurer `src` et `install` depuis l'archive `pre-<sha>` de `deploy.sh`. L'extraction n'efface rien : les fichiers que la nouvelle version a ajoutés sous `src` et `install` restent (liste plus bas).
6. **En dernier**, restaurer `device.json` depuis `<sauvegarde>` vers `<racine>/src/config/device.json`, puis l'ancien `.env` vers `<racine>/.env` (l'étape 7 l'a modifié) avant tout redémarrage.
7. Contrôler les **destinations**, pas seulement les copies sauvegardées : `sha256sum -c <sauvegarde>/destinations.sha256`. Une somme qui diffère signifie que la restauration n'a pas eu lieu : ne pas redémarrer.
8. `sudo systemctl daemon-reload`, puis `sudo systemctl start inkypi`, et refaire les contrôles de l'étape 8 adaptés à l'ancienne version.

### Ce que le rollback ne restaure pas

- les paquets `apt` installés par `update.sh`, dont `zram-tools` et `earlyoom` ;
- les services activés et démarrés par `update.sh` (`zramswap`, `earlyoom`) ;
- le fichier `/etc/default/zramswap` écrit par `update.sh` ;
- les fichiers que la nouvelle version a ajoutés sous `src` et `install` ;
- l'état de l'écran : il garde l'image affichée jusqu'au prochain rafraîchissement de l'ancienne version.

Décision ops, à prendre à l'étape 2 : soit accepter ces restes (un swap compressé et `earlyoom` sont tolérés par l'ancienne version, à confirmer à la relecture), soit les retirer à la main après le rollback (`systemctl disable --now`, `apt remove`, suppression de `/etc/default/zramswap`), soit prendre une image de la carte SD avant la coupure, seule garantie d'un retour complet.

## Fenêtre de coupure

L'écran n'est plus rafraîchi pendant cette fenêtre. Prévoir une durée de l'ordre de la demi-heure, dominée par `update.sh` (apt et pip sur un Pi Zero 2 W). `device.json` est réécrit par l'application à chaque rafraîchissement, sans écriture atomique : une coupure de courant pendant les étapes 4 à 6 sans la sauvegarde de l'étape 4 fait perdre la configuration.
