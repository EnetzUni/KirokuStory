# Kiroku Story

A Django website for running D&D campaigns: a character sheet made for the phone during play, the campaign diary (chronicles, timeline, quests, role-play scenes and an investigation board), a world wiki with secrets, maps with the party's route, character creation with the master's approval, and combat turns. It is built on the SRD 5.2.1 rules catalog in Spanish, our homemade weapons and our own classes, and plays with **Aegis**, our variant of the SRD with our own rules; each campaign can switch to the standard SRD 5.2.1 instead.

- Project `KirokuStory`, one app `CampaignManager` (rules catalog and campaigns together; see «Deviations»).
- Data model: [`data-model.md`](data-model.md) and the diagram `ER.drawio`.

## Installation

Python 3.13 and the packages in `requirements.txt` (Django 6.1.3, Pillow 13.0.0).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py cargar_demo       # rules + homemade weapons + demo campaigns + demo images
python manage.py runserver
```

Open http://127.0.0.1:8000/ and log in with a demo user.

- `cargar_demo` loads, in order, `datos_iniciales_srd`, `armas_caseras`, `clases_aegis` and `demo_campana`, and copies the demo images (`CampaignManager/fixtures/demo_media/`) into `media/`. The fixtures have fixed ids, so it refuses to run on a database that already has data: delete `db.sqlite3` and run `migrate` again.
- `cargar_sokoku` (optional, on the database with the real campaign) loads the 82 NPCs of the master's bestiary and the sheets of Zarek, Leah, Thalion and Balasar into «Infinity Blade» (`--campana` for another one). It can run again: it keeps the sheets it finds, and `--actualizar` rewrites them.
- Tests: `python manage.py test CampaignManager` (210 tests).
- The interface is in Spanish; English is at `/en/…` or with the selector in the footer.

### Demo users

All of them use the password **`mesa-demo-2026`**.

| User | Role |
|---|---|
| `carlos` | Master of the three campaigns (with `iker` in «Ecos de Kioto»). Staff, in the «Reglas» and «Campañas» groups: can use `/admin/` |
| `lucia` | Player: Aria in «La Corona de Ceniza» and «Ecos de Kioto» |
| `iker` | Player: Brom in «La Corona de Ceniza» and «Prólogo en Puerto Gris»; co-master of «Ecos de Kioto» |
| `ane` | Player: Kaida, and Hana in «Ecos de Kioto» |
| `unai` | Player of «La Corona de Ceniza» without a character yet: try the character creator with him |
| `nerea` | Admin role «Reglas» only: in `/admin/` she edits the rules catalog (races, classes, abilities and feats, spells, items…) and cannot touch the campaigns |
| `mikel` | Admin role «Campañas» only: in `/admin/` he edits the campaigns, characters and their contents, and only views the rules catalog |

«La Corona de Ceniza» is the full campaign (active, with a fight in progress); «Ecos de Kioto» is paused and «Prólogo en Puerto Gris» is finished. To manage users, create a superuser with `python manage.py createsuperuser`.

### Production settings

Settings come from environment variables (see `.env.example`); the defaults are only for development.

| Variable | Default | |
|---|---|---|
| `DJANGO_DEBUG` | `True` | With `False`, `DJANGO_SECRET_KEY` is required and HTTPS, secure cookies, HSTS, `nosniff` and `X-Frame-Options: DENY` are turned on |
| `DJANGO_SECRET_KEY` | a development key, only when debugging | |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` when debugging | Comma-separated |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty | Comma-separated, with scheme |

## Where each E4a option is

| E4a option | Where |
|---|---|
| **1. JavaScript: events, effects, AJAX** | `CampaignManager/static/js/mesa.js` (jQuery 3.7.1 next to it). Forms marked `data-ajax` are sent with `$.post` and the `X-CSRFToken` header; the view answers JSON with the HTML of the part that changed, rendered with the same partial template as the page (`ajax.respond()`). Without JavaScript the same forms post and redirect. Used by every counter of the sheet (`partials/sheet_panel.html`), the quest objectives, the board (move, join, reveal; `partials/board.html`), the turns (`partials/turns_list.html`, refreshed every 5 s with `data-refrescar`), scene messages and placing locations on the map (`data-colocar`). The floating preview of `[[links]]` asks `world_preview` for the summary. Effects: the floating message of each action fades in and out (`fadeIn`/`fadeOut`), and the value that changed is highlighted (`.cambio`); with «reduce motion» both are off (`$.fx.off`). |
| **2. Class-based views** | `ListView`/`DetailView` in `views.py` (rules catalog); `TemplateView`, `DetailView`, `CreateView` and `UpdateView` in `views_home.py`, `views_diary.py`, `views_world.py`, `views_map.py`, `views_characters.py` and `views_turns.py`. Permissions in mixins: `CampaignMemberMixin` and `MasterRequiredMixin` (`permissions.py`). Small AJAX endpoints are function views with the `campaign_member` decorator. |
| **3. Public forms** | `forms.py`: `ModelForm`s for chronicles, timeline events, board notes, scenes, quests (with an inline formset of objectives), world pages (no `parent` cycles, parents only places or factions, map location only for places), maps and locations; a form per step of the character creator (`RaceStepForm`… `EquipmentStepForm`) with the standard array and point buy checks. The model `clean()` methods keep the house rules (one armor, unique abilities). |
| **4. Admin with several roles** | `admin.py`: `list_display`, searches, filters, `list_select_related` and inlines everywhere: objectives inside a quest, dice rolls and armor damage inside an attack, subraces and granted abilities inside a race, granted abilities and add-ons inside a class, abilities, inventory and effects inside a character, posts inside a scene, locations inside a map, combatants inside an encounter. Groups in the data migration `migrations/0018_admin_groups.py`: **Reglas** edits the rules catalog (21 models); **Campañas** edits the 23 campaign models and only views the catalog. Demo staff users: `nerea` has only «Reglas», `mikel` only «Campañas» and `carlos` both, so the two roles can be compared side by side (`AccessTests.test_admin_roles_have_different_permissions`). |
| **5. Initial data for every entity** | `fixtures/datos_iniciales_srd.json`, `fixtures/armas_caseras.json`, `fixtures/clases_aegis.json` (our Arcane Warrior class) and `fixtures/demo_campana.json` (299 objects: users, armor types and armors, characters, templates and copies, and every campaign model), loaded by `management/commands/cargar_demo.py`. The demo fixture is generated by `management/commands/generar_demo.py` from the prototype's data. |
| **6. Multilingual** | `LocaleMiddleware`, `i18n_patterns` with Spanish unprefixed (`KirokuStory/urls.py`), `{% translate %}` in every template, `gettext_noop` for the labels defined in Python, and the English catalog `locale/en/LC_MESSAGES/django.po` (942 strings). Language selector in the footer (`base.html`, Django's `set_language`). Campaign contents are not translated. |
| **7. Performance and security** | See «Audit» below. |

## Audit

Run on 17 November 2026 and again on 24 November 2026, after the last features.

- **`python manage.py check --deploy`** with `DJANGO_DEBUG=False`, a random `DJANGO_SECRET_KEY` and real hosts: *System check identified no issues (0 silenced).*
- **`pip-audit -r requirements.txt`**: *No known vulnerabilities found.* Auditing the virtual environment found 4 advisories in `pip` 26.0.1 itself; it was upgraded to 26.3 and the audit is clean. `asgiref` and `sqlparse` are now pinned too.
- **`bandit -r CampaignManager KirokuStory -x CampaignManager/migrations,CampaignManager/tests.py`**: no high or medium issue left; 5 low ones remain on purpose.
  - Fixed: the `[[link]]` renderer (`wiki.py`) and the `wiki_inline` tag used `mark_safe` (6 medium). Everything was already escaped, but they now build the HTML with `format_html` and add safe strings together.
  - Fixed: the avatar colors use MD5 only to pick a color, and the sheet uses SHA-1 only to tell whether its panel changed: both now with `usedforsecurity=False` (2 high).
  - Kept and marked `# nosec`: the sheet page (`SheetView`) receives its panel already rendered by `render_to_string`, so the template engine has escaped it; it is rendered once to also fingerprint it, and marked safe to be inserted in the page (2 medium, the same line).
  - Kept (B105, low): the development `SECRET_KEY`, used only when `DEBUG` is on (production refuses to start without its own key); the demo password `mesa-demo-2026`, which is documented; and three form labels «Secreto del máster», which are not passwords.
- **N+1 queries**: query counts were measured on every page with `CaptureQueriesContext`. Three repeated queries were fixed:
  - The campaign role is remembered on the campaign for the whole request: it was asked 7 times.
  - The «te toca escribir» badge now takes two queries with a subquery, instead of one per scene.
  - The turns list loads every combatant's armor and states at once (31 → 21 queries).
  - Lists use `select_related`/`prefetch_related`, and there are indexes on campaign status, character type and status, map location status and active encounters.
- **Hidden content**: `CampaignManager/tests.py` (`HiddenContentTests`) logs in as a player and visits every page and JSON answer of the demo. Its text must contain none of the secrets: hidden objectives, classified notes, page secrets and hidden pages, hidden events, master's notes, unpublished chronicles and other people's whispers. The same pages, opened by the master, must contain all of them.
- **Mobile**: the sheet, the turns, the board and the creator were checked at 390 px in Firefox: no horizontal scroll, touch targets of at least 44 px.

## Phase notes

1. **Base.** Settings read from environment variables, `requirements.txt`, Django 6.1. Kept the E3 models; added `rules.py` with the open decisions (XP table, magic points, point buy) and `CharacterAbility` (uses and cooldowns) as the through table of `Character.abilities`, moving the existing data with migrations 0015–0017.
2. **Campaign.** Campaign models (`campaign_models.py`) with their `visible_for()` querysets; demo fixture and `cargar_demo`; `base.html` with the design (cream and forest green, Cardo, flat), login, campaign selector, home page with the cover, the last chronicle, the party and the active quests.
3. **Sheet and characters.** Character list (party, campaign NPCs, NPC templates copied to the campaign or straight into the turns); mobile-first sheet with every counter saved by AJAX (hit points with death saves, AP, inspiration, magic points, uses and rests, spells with slot choice, inventory, gold, equipment with one armor, states, notes); six-step creator with the draft in the session, a Django form per step, and the master's approval.
4. **Diary.** Chronicles with `[[links]]`, timeline with hidden events, quests with hidden objectives, scenes with `/tirar` rolled on the server and whispers, and the board with red threads and classified notes.
5. **World and map.** Wiki with a tree, breadcrumbs, secrets and «Aparece en»; maps with an image, locations placed with a click, states (hidden, known, visited) synced with their pages, and the party's route by session.
6. **Turns.** Start, add characters, templates or free-named enemies, initiative, sort, «Siguiente», end. Closing a round ticks cooldowns and effects. Players see the order by polling, change only their own characters, and never receive enemy hit points.
7. **Course options and polish.** Admin with groups, English translation, the E3 rules catalog translated to Spanish and limited to what the reader may see (it listed every campaign and character without logging in), performance, security audit, tests and this README.
8. **Experience and levels.** The master gives XP from the sheet («Dar XP», «Quitar XP») or to several party members at once from «Personajes» («Repartir experiencia»); before, only the admin could. The level is now stored (migration 0019 sets each existing character's level from their XP). When the XP reaches the next level, the player sees «Subir al nivel N» on the sheet and a pill in the character list, and goes up one level at a time choosing what it gives (`level_up` in `views_characters.py`, `LevelUpForm` in `forms.py`, `level_up.html`): hit points (house rule: 1d4 rolled at the table in front of the master, the player types the result, + Constitution), the subclass at level 3, and every 4 levels from level 4 the ability score improvement (two +1, max 20) or a feat (an ability marked «feat» in the admin). Masters can do it for any character of their campaign.
9. **Magic and spells by the master.** The maximum magic points come from the class (`CharacterClass.magic`: full caster, half caster, pact magic or none) and the level (`rules.class_magic_points`), plus an adjustment the master sets on the sheet; migration 0020 keeps any maximum a master had set as that adjustment. Spells are not chosen when levelling up: the master teaches or removes them from the sheet's «Conjuros» tab.
10. **Unique skills.** Each character can have one (`UniqueSkill`, game rule 17). The player describes it in a new step of the creator («Habilidad única», optional) or later from the sheet («Rasgos»); the master sees a counter on «Personajes» (and on the folded menu's button on the phone) and approves it, changes it or rejects it with a note, from «Personajes» or the sheet. Once approved it is a unique ability of the character, shown highlighted at the top of «Rasgos». In the demo, Aria's («Lectora de runas») is approved, Kaida's waits for `carlos`, and Brom has none yet.
11. **Aegis and SRD.** Each campaign chooses its ruleset on its home page («Reglamento», `Campaign.ruleset`, migration 0027; existing campaigns are Aegis). **Aegis** is our variant of the SRD 5.2.1: 1d4 + Constitution hit points rolled at the table, the improvement or a feat every 4 levels, spells only from the master and unique skills. The **standard SRD 5.2.1** uses the class hit die (fixed or rolled), the SRD improvement levels (plus the fighter's and rogue's, and the epic boon at 19), lets the player pick the new spells its class table gives, and has no unique skills. The menu's rules link is now «Reglas».
12. **Rumores.** A new menu section, «Rumores de <campaign>» (`views_rumours.py`, `rumours.html`): the master writes rumours, official notices, news and legends (who said them, where, in which session, and how much truth they hold, which only masters see) and publishes them when the party hears them. Each campaign has its own categories with a colour (`RumourCategory`), which the master creates, renames, recolours and deletes; the list filters by category by AJAX.
13. **Weather layer on the map.** «Tiempo real» overlays the live weather of real places on the fictional map (`weather.py`, `WeatherRegion`). The master links a location or a drawn region to a country, city or coordinates (with suggestions from Open-Meteo's geocoding); each region shows its condition, temperature, wind and rain, and is shaded with a moon where it is night. One clock for the campaign (`Campaign.time_zone`, in the campaign wizard). Open-Meteo is called on the server, once per map every 20 minutes at most; if it is down, the last values stay. The tests replace the API with a fake, so they do not need the network.
14. **Arcane Warrior and portraits.** A class of our own for Aegis, made from Leah's sheet (a party member): the Explorador with **combat magic** instead of spells (`CharacterClass.Magic.COMBATE`, game rule 21 in `data-model.md`). Its magic points «a» are 10 + 2 per level, and it spends them on the 20 add-ons of its class (`ClassAddon`: sword, bow, buffs and defence), whose effects are saved against with Dexterity, against 12 + its Dexterity modifier. On the sheet the «Conjuros» tab becomes «Añadidos», each with a «Usar» button that spends its cost (Escudo lets the player choose from 1 to 5); the paper sheet and the rules catalog show the add-ons table as on the class sheet. It keeps the Explorador's hit die, subclass (Cazador) and every class ability except «Lanzamiento de conjuros», which «Magia de combate» replaces. SRD campaigns do not offer it in the creator (`CharacterClass.aegis_only`). It comes in `fixtures/clases_aegis.json`, and migration 0032 makes it from the Explorador in databases that already had the SRD catalog. «Personajes» now opens each party card with the character's portrait (or its initial on its colour) and its name over it, with «Añadir retrato» while it has none; the paper sheet can upload the portrait too.
15. **Colours and the battle table.** The party's route is drawn as small arrows, a short rectangle with a pointed tip, one after another, each stretch in its own colour (`PartyMove.color`), which the master picks in «Editar el camino» (a new stretch keeps the colour of the one before); they keep their size on screen at any zoom. Adventures and missions get an optional colour (a palette of swatches, none or any other) and a small header image (`Quest.color`, `Quest.cover`): the colour marks their left edge in «Aventuras», their bar on the timeline and their dot on the home page, and a mission without one takes its adventure's; the cover is a header, also over the folded ones. «Turnos» is now a table of cards: the party on one side and those who fight it on the other (`Combatant.side`, which the master can flip for an NPC ally), each card with its portrait or initial, its place in the order, initiative, hit points and AP, «Su turno» / «Después» and the knocked-down or dead seal, and its quick actions (a number and Enter deals the damage). Above, the round, whose turn it is with «Siguiente turno», and the order of the fight as a strip of portraits. On a phone the sides go one under the other and the cards lie down.
16. **The bestiary and the party's sheets.** `python manage.py cargar_sokoku` loads `CampaignManager/data/sokoku.json` into «Infinity Blade»: the 80 NPCs of the master's «Bestiario de NPCs — Sokoku» as templates of the campaign, ready to copy into a fight. Each has its kind (Mortal, Inmortal, Bestia, Reforjado) and its rank (Tropa, Veterano, Élite, Jefe…) as the master's own race and class, its own speed (`Character.own_speed`), its armor, its weapons as copies of the house weapons with its numbers (or pieces of its own for bites, spells and artifacts), its traits and reactions as abilities with their uses or cooldown, and its skills and tactics («En su fase») in its notes. And the sheets of Zarek, Leah and Thalion, from their nivel20 sheets and their players' «extra» files, with their portraits (`data/sokoku_retratos/`); what the catalog lacked for them is added (the Artífice and its Artillero, the Acechador en la penumbra of the Arcane Warrior, five feats and six spells from outside the SRD). World pages and sheets now point to each other (`Page.sheets`, «Fichas» in the page form, with a search box): the page shows its sheets with their numbers (players, only the party's) and each sheet its pages under «En el mundo»; in the diary, `[[Name]]` of an NPC opens its sheet for masters. A weapon's description shows under its name in «Acciones», and the master's sheet editor accepts scores up to 40 (Ausar's Dexterity is 34).
17. **Balasar, turns by hand and wounds.** `cargar_sokoku` also loads Balasar (Julen's druid of the Círculo de la Luna, level 10) into «Infinity Blade», with his player, his own speed (wood elf, 12 m), his dart thrower, his house spell «Curación dañina» and his unique skill; the Balasar of the Alforge sample stays as it was. In «Turnos» the master decides who acts: «Le toca» gives the turn to any card, ‹ › move a card one place in the order (whoever has the turn keeps it), and «Nueva ronda» starts a round when the master says so (cooldowns and effects tick, the first of the order acts); «Ordenar por iniciativa» is still there. Only the master gets the numbers of the enemy side; instead, every picture of the fight (cards, the order strip, «Turno de») takes a red veil that thickens as the fighter loses hit points, in four steps and never solid, for the party and the enemies alike.
18. **Shayne and Aurox, and states on the cards.** The bestiary grew to 85 pages: `cargar_sokoku` loads its two new sheets, Shayne and Aurox, a pair that fights as one (`Character.acts_with`, migration 0035). Each carries the pair's story, «Lo que se cuenta» and «Cómo pelean juntos»; Shayne her six orders (¡Trae! every 2 rounds, Juntos once per fight) and Aurox his grab, his taunt and his reactions. In «Turnos» a pair side by side takes one turn: a copy of one brings the other, «Turno de» names both and «Siguiente», «Le toca», ‹ › and the initiative order treat them as one. Tokiasa Yarikawa's sheet keeps only that name: the loader renames a sheet loaded under an older one (`was_like`) and rewrites it, and his page links are his own page and Yarikawa. Every combat card has a list of states at its foot and shows the ones it suffers as tags with a ×, and as an animation over its picture (blood, fire, frost, poison…, in CSS, still with reduced motion); enemies without a sheet can suffer them too (`ActiveEffect.combatant`). The house states the bestiary uses (Aterrado, Atado, Congelado, Quemado, Sangrando, Suspendido) are in the catalog.
19. **The page editor of the world.** Creating, editing and deleting a page of the world is one compact screen (`world_edit.html`): the title, the category as five coloured choices, and the two texts (what the group knows, the master's secret) on the left; on the right the page as it will look, live (its colour or image, its title, whether the group sees it), with the switch to show it to the group, its image, where it is, its summary and its sheets; save, cancel and delete stay at the foot. «Enlazar» links words of a text to another page (choose them and press it, or Ctrl + K, or type `[[`): a finder lists the pages and sheets as you type, and a link can show words of its own, `[[Shayne (nómada)|la niña]]` (`wiki.py`). Under each text, its links as chips: one finds it in the text, its × unlinks it and keeps the words; a link to a page that does not exist yet is dashed. Deleting a page (from the editor or the page) says first what it leaves behind: the pages inside it go loose, its links stay as words, its sheets and map place stay. A category's cards start a new page or, with the pencil, edit one. Without JavaScript the same form works and links are written by hand.

## Deviations from the spec and decisions

- **One app.** The spec asks for two apps (`dnd` and `campana`). The project keeps its existing name and its single app `CampaignManager`, so the fixtures keep their `CampaignManager.*` labels. The «Reglas»/«Campañas» split is done in the admin groups.
- **Kept from E3:** `Character.campaigns` stays many-to-many (as in the diagram); `Item.template` stays `PROTECT`; `Weapon.to_hit` keeps our formula format (`"2 + dex"`, `"-3 + dex x2"`).
- **Added beyond the spec:** `Session.master_notes`, two more event kinds (`mision`, `secreto`), `Character.status` for the approval. Avatars without a portrait get a color calculated from the character.
- **Not modeled:** extra fields of the prototype's wiki pages (attitude, linked characters), sub-maps and location descriptions (the page holds them), out-of-character messages, and posting as a named NPC (the master posts as the narrator). The prototype's «next session» and attendance features are out of the spec.
- **Creator:** level 1 hit points = the class hit die's maximum + the Constitution modifier, armor class = 10 + the Dexterity modifier (SRD). No starting gold is added: it can be given from the sheet.
- **Turns:** only the master gets the hit points, AP and how long the states last of the enemy side and of NPCs (the states themselves show on every card), so players judge a fight from what the master tells them, not from numbers. Players see their party's characters and their own, on whichever side it stands.

### Open questions

- **Rule 3:** how much damage the AP stop is not defined yet. `rules.ap_against_attack()` and `ap_after_attack()` give the AP the damage is dealt against and the AP afterwards, but the damage is not reduced automatically.
- **Levels:** with the SRD table, Balasar (`populate`, 50,250 XP) is level 9, while his sheet says level 10. The level is stored now, so it can be set to 10 in the admin, or the master can give him the 13,750 XP up to 64,000 and he levels up.
- **Feats:** the SRD fixture has no feats yet. They are added in the admin as abilities with «feat» ticked (and «feat min level» 19 for epic boons); `cargar_sokoku` adds the five the party uses. A feat that raises an ability score does not do it by itself: the master changes the score.
- **Leah's sheet** lists features of the 2014 Explorador and of the «Acechador en la penumbra» subclass, which are not in the SRD 5.2.1: the Arcane Warrior follows the catalog's Explorador. `cargar_sokoku` gives her the subclass (its level 3 features), the 2014 features that do not need spells, her feats and her unique skill.
- **The four sheets:** magic points come from the class (16 for Zarek and Thalion, 28 «a» for Leah, 41 for Balasar), not from counting spell slots as the players do (9 and 15). Balasar's XP is the 70,250 of his nivel20 sheet (his «extra» file says 64,000), and «Curación dañina» still needs its casting time and range. Zarek's breastplate is given 4 AP, a medium armor in the table rules, until the master says; his revolvers have no range yet and his bullets are not in the inventory. No player is assigned to them: Enetz10, carosfir and Pojon have no account here.
- **The bestiary:** the tables the master wrote inside an entry (Gregor's trophies, Spark's «Mente», the Crow Mauler's parts, Scarlet's affinity) are abilities of the NPC. Two page links are guesses to check: the giant samurai of Yarikawa with «El inmortal de armadura plateada», and Jimmy Hopkings with «Hopkins». Shayne and Aurox have their own pages, «Shayne (nómada)» and «Aurox» (the world's «Shayne» is another person, a QIP researcher); the players know only that she steals to survive, roams and slips away, and «Aurox» stays hidden from them.
- **Spells granted by class or race:** abilities such as «Conjuros del dominio de la vida» list their spells only in their text, so they are not added automatically: the master teaches them from the sheet.

## Delivery

One compressed file named after the group and the delivery, `IW-BI-01-E4.zip` (`.zip`, `.gz`, `.tgz` or `.rar`), with the documentation and one folder per project, each with everything it needs to run:

```
IW-BI-01-E4.zip
├── Memoria.pdf          the documentation (E5)
├── E4a/                 this Django project (the folder that holds manage.py)
└── E4b/                 the Vue SPA (contact list)
```

- Leave out of `E4a/`: `db.sqlite3`, `.venv/`, `media/`, `staticfiles/` and every `__pycache__/`. The demo images are inside `CampaignManager/fixtures/demo_media/`, and `cargar_demo` rebuilds the database and copies them to `media/` (see «Installation»).
- Dates: the project before 5 December; the documentation can be added until 11 December.
