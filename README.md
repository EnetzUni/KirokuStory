# Kiroku Story

A Django website for running D&D campaigns: a character sheet made for the phone during play, the campaign diary (chronicles, timeline, quests, role-play scenes and an investigation board), a world wiki with secrets, maps with the party's route, character creation with the master's approval, and combat turns. It is built on the SRD 5.2.1 rules catalog in Spanish and our homemade weapons.

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

- `cargar_demo` loads, in order, `datos_iniciales_srd`, `armas_caseras` and `demo_campana`, and copies the demo images (`CampaignManager/fixtures/demo_media/`) into `media/`. The fixtures have fixed ids, so it refuses to run on a database that already has data: delete `db.sqlite3` and run `migrate` again.
- Tests: `python manage.py test CampaignManager` (59 tests).
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
| **1. JavaScript: events, effects, AJAX** | `CampaignManager/static/js/mesa.js` (jQuery 3.7.1 next to it). Forms marked `data-ajax` are sent with `$.post` and the `X-CSRFToken` header; the view answers JSON with the HTML of the part that changed, rendered with the same partial template as the page (`ajax.respond()`). Without JavaScript the same forms post and redirect. Used by every counter of the sheet (`partials/sheet_panel.html`), the quest objectives, the board (move, join, reveal; `partials/board.html`), the turns (`partials/turns_list.html`, refreshed every 5 s with `data-refrescar`), scene messages and placing locations on the map (`data-colocar`). The floating preview of `[[links]]` asks `world_preview` for the summary. Effects: only a short highlight of the value that changed (`.cambio`), turned off with «reduce motion». |
| **2. Class-based views** | `ListView`/`DetailView` in `views.py` (rules catalog); `TemplateView`, `DetailView`, `CreateView` and `UpdateView` in `views_home.py`, `views_diary.py`, `views_world.py`, `views_map.py`, `views_characters.py` and `views_turns.py`. Permissions in mixins: `CampaignMemberMixin` and `MasterRequiredMixin` (`permissions.py`). Small AJAX endpoints are function views with the `campaign_member` decorator. |
| **3. Public forms** | `forms.py`: `ModelForm`s for chronicles, timeline events, board notes, scenes, quests (with an inline formset of objectives), world pages (no `parent` cycles, parents only places or factions, map location only for places), maps and locations; a form per step of the character creator (`RaceStepForm`… `EquipmentStepForm`) with the standard array and point buy checks. The model `clean()` methods keep the house rules (one armor, unique abilities). |
| **4. Admin with several roles** | `admin.py`: `list_display`, searches, filters, `list_select_related` and inlines everywhere: objectives inside a quest, dice rolls and armor damage inside an attack, subraces and granted abilities inside a race, abilities, inventory and effects inside a character, posts inside a scene, locations inside a map, combatants inside an encounter. Groups in the data migration `migrations/0018_admin_groups.py`: **Reglas** edits the rules catalog (20 models); **Campañas** edits the 20 campaign models and only views the catalog. |
| **5. Initial data for every entity** | `fixtures/datos_iniciales_srd.json`, `fixtures/armas_caseras.json` and `fixtures/demo_campana.json` (271 objects: users, armor types and armors, characters, templates and copies, and every campaign model), loaded by `management/commands/cargar_demo.py`. The demo fixture is generated by `management/commands/generar_demo.py` from the prototype's data. |
| **6. Multilingual** | `LocaleMiddleware`, `i18n_patterns` with Spanish unprefixed (`KirokuStory/urls.py`), `{% translate %}` in every template, `gettext_noop` for the labels defined in Python, and the English catalog `locale/en/LC_MESSAGES/django.po` (516 strings). Language selector in the footer (`base.html`, Django's `set_language`). Campaign contents are not translated. |
| **7. Performance and security** | See «Audit» below. |

## Audit

Run on 17 November 2026.

- **`python manage.py check --deploy`** with `DJANGO_DEBUG=False`, a random `DJANGO_SECRET_KEY` and real hosts: *System check identified no issues (0 silenced).*
- **`pip-audit -r requirements.txt`**: *No known vulnerabilities found.* Auditing the virtual environment found 4 advisories in `pip` 26.0.1 itself; it was upgraded to 26.3 and the audit is clean. `asgiref` and `sqlparse` are now pinned too.
- **`bandit -r CampaignManager KirokuStory -x CampaignManager/migrations`**: it found 10 issues; 7 were fixed and 3 low ones remain on purpose.
  - Fixed: the `[[link]]` renderer (`wiki.py`) and the `wiki_inline` tag used `mark_safe` (6 medium). Everything was already escaped, but they now build the HTML with `format_html` and add safe strings together, so there is no `mark_safe` left.
  - Fixed: the avatar colors use MD5 only to pick a color: now `md5(..., usedforsecurity=False)` (1 high).
  - Kept (B105, low): the development `SECRET_KEY`, used only when `DEBUG` is on (production refuses to start without its own key); the demo password `mesa-demo-2026`, which is documented; and the form label «Secreto del máster», which is not a password.
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

## Deviations from the spec and decisions

- **One app.** The spec asks for two apps (`dnd` and `campana`). The project keeps its existing name and its single app `CampaignManager`, so the fixtures keep their `CampaignManager.*` labels. The «Reglas»/«Campañas» split is done in the admin groups.
- **Kept from E3:** `Character.campaigns` stays many-to-many (as in the diagram); `Item.template` stays `PROTECT`; `Weapon.to_hit` keeps our formula format (`"2 + dex"`, `"-3 + dex x2"`).
- **Added beyond the spec:** `Session.master_notes`, two more event kinds (`mision`, `secreto`), `Character.status` for the approval. Avatars without a portrait get a color calculated from the character.
- **Not modeled:** extra fields of the prototype's wiki pages (attitude, linked characters), sub-maps and location descriptions (the page holds them), out-of-character messages, and posting as a named NPC (the master posts as the narrator). The prototype's «next session» and attendance features are out of the spec.
- **Creator:** level 1 hit points = the class hit die's maximum + the Constitution modifier, armor class = 10 + the Dexterity modifier (SRD). No starting gold is added: it can be given from the sheet.
- **Turns:** players do not receive the hit points of enemies or NPCs.

### Open questions

- **Rule 3:** how much damage the AP stop is not defined yet. `rules.ap_against_attack()` and `ap_after_attack()` give the AP the damage is dealt against and the AP afterwards, but the damage is not reduced automatically.
- **Levels:** with the SRD table, Balasar (`populate`, 50,250 XP) is level 9, while his sheet says level 10. The level is stored now, so it can be set to 10 in the admin, or the master can give him the 13,750 XP up to 64,000 and he levels up.
- **Feats:** the SRD fixture has no feats yet. They are added in the admin as abilities with «feat» ticked (and «feat min level» 19 for epic boons). A feat that raises an ability score does not do it by itself: the master changes the score.
- **Spells granted by class or race:** abilities such as «Conjuros del dominio de la vida» list their spells only in their text, so they are not added automatically: the master teaches them from the sheet.

## Delivery

Zip the project folder as `IW-BI-xxx-E4.zip` **without** `db.sqlite3`, `.venv/` or `media/`. The demo images are inside `CampaignManager/fixtures/demo_media/`, and `cargar_demo` copies them to `media/`.
