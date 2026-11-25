# Data model: D&D campaign manager

Text version of the entity-relationship diagram (`ER.drawio`), written as context for building the Django project. If the diagram and this file ever disagree, update both.

## Conventions

- Every model has an auto-increment primary key `id`.
- Diagram types map to Django fields as follows: `int` → `IntegerField`, `string` → `CharField`, `text` → `TextField`, `bool` → `BooleanField`, `date` → `DateField`.
- A column written `something_id` is a foreign key. In Django, declare it as `something = models.ForeignKey(...)`; Django adds the `_id` to the database column itself.
- Words in italics (*contiene*, *aplica*…) are the relationship labels used on the diagram.

## Models at a glance

| Area | Models |
|---|---|
| Users and campaigns | User, Campaign |
| Characters | Character, Race, Subrace, CharacterClass, Subclass |
| Abilities | Ability, CharacterAbility, RaceAbility, SubraceAbility, ClassAbility, SubclassAbility, UniqueSkill |
| Spells and combat | Spell, Component, Attack, DiceRoll, ArmorDamage |
| Effects | Effect, ActiveEffect |
| Items and equipment | Item, ItemEffect, Inventory, Weapon, Armor, ArmorType |
| Campaign contents (E4) | Session, TimelineEvent, Page, Quest, QuestObjective, BoardCard, BoardLink, Scene, Post, Map, MapLocation, PartyMove, WeatherRegion, RumourCategory, Rumour, Encounter, Combatant |

CharacterAbility, RaceAbility, SubraceAbility, ClassAbility, SubclassAbility, ItemEffect, Inventory and ActiveEffect are **link tables** (yellow in the diagram): they join two models and carry extra data of their own.

---

## Users and campaigns

### User

Use Django's authentication system: a custom user model `class User(AbstractUser)`, with `AUTH_USER_MODEL` set before the first migration. It already provides `username`, `email` and a securely hashed `password`, so these are not declared by hand.

- Directs (*dirige*) many Campaigns as a Master. A Campaign can have several Masters.
- Plays in (*juega en*) many Campaigns. A Campaign has many players. A player can join a campaign before having a character there.
- Controls (*controla*) many Characters.

### Campaign

| Field | Type | Notes |
|---|---|---|
| name | string | |
| description | text | |
| start_date | date | |
| status | string | One of `activa`, `pausada`, `terminada` |
| ruleset | string | `aegis` (default) or `srd`: the rules the campaign plays with (rule 18). The master changes it on the home page |
| cover | image | **Optional.** Cover picture of the home page (`portadas/`) |
| notice | text | **Optional.** «Aviso del máster», shown on the home page |
| world_day | string | **Optional.** The day in the world, e.g. «Día 9 de Brumaluz» |
| world_year | string | **Optional.** The year in the world |
| time_zone | string | IANA name, default `Europe/Madrid`. The one clock everybody in the campaign shares (rule 20). Checked by a validator, not stored as choices: the list depends on the machine |

- Has many Masters and many players (both are Users, see above).
- Contains (*contiene*) many Characters. A Character can belong to many Campaigns, or to none. Deleting a campaign only removes the link: its characters are kept, and those left without any campaign are shown apart.

---

## Characters

### Character

| Field | Type | Notes |
|---|---|---|
| name | string | |
| xp | int | Given by the master |
| level | int | 1 to 20. Level reached: it goes up when the player levels up (see game rule 16), so it can be behind the XP |
| hp | int | Maximum hit points |
| temp_hp | int | Current hit points |
| ac | int | Armor class |
| temp_ap | int | Current armor points (see game rules) |
| inspiration | int | |
| strength | int | |
| dexterity | int | |
| constitution | int | |
| intelligence | int | |
| wisdom | int | |
| charisma | int | |
| player_id | FK → User | **Optional.** A character may have no user. |
| char_class_id | FK → CharacterClass | Required. Exactly one class: no multiclassing. |
| subclass_id | FK → Subclass | **Optional.** Must be a subclass of the character's class |
| race_id | FK → Race | Required |
| subrace_id | FK → Subrace | **Optional.** Must be a subrace of the character's race |
| is_npc | bool | True for NPCs (see NPCs below) |
| template_id | FK → Character | **Optional.** Only for NPC copies: the NPC template it was made from |
| status | string | `aprobado` (default) or `pendiente`: a player's new character waits for the master's approval |
| portrait | image | **Optional.** (`retratos/`) |
| background | text | **Optional.** The character's background (*trasfondo*) |
| notes | text | **Optional.** The player's own notes |
| death_successes | int | 0 to 3. Marked only at 0 hit points; cleared when hit points go above 0 or on a long rest |
| death_failures | int | 0 to 3. Same as above |
| magic_points | int | Current magic points |
| magic_points_bonus | int | Default 0. The master's adjustment, positive or negative, to the magic points the class gives at the character's level |

- Belongs to many Campaigns (*contiene*, many-to-many), or to none. There is no `campaign_id` column.
- Has (*posee*) many Abilities through CharacterAbility, which stores their uses. An Ability can be held by many Characters, unless it is unique: then only one character per campaign (see game rules).
- Knows (*conoce*) many Spells.
- Carries (*lleva*) Items through Inventory.
- Suffers (*sufre*) Effects through ActiveEffect.

Not stored, calculated when needed:

- **speed**: from the character's Race. Subraces have no speed of their own.
- **maximum AP and maximum magic AP**: from the equipped armor (see game rules).
- **initiative**: from dexterity.
- **passive perception**: from wisdom.
- **maximum magic points** (`magic_points_max`): what the class gives at the character's level (rule 15) + `magic_points_bonus`, at least 0. With 0, the character does not use magic points.
- **level the XP is enough for** (`xp_level`): from xp, with the SRD table (300, 900, 2700…) in `CampaignManager/rules.py` (`XP_THRESHOLDS`). While it is above `level`, the character can level up (`can_level_up`).

### NPCs: templates and copies

NPCs are Characters with `is_npc = true`. There can be many of the same NPC (four goblins) and an NPC can be reused in many campaigns, so NPCs work with templates and copies (*es copia de*, Character → Character):

- **Template:** an NPC with no `template`, for example "Goblin". It is defined once and can be used in any campaign. A one-off NPC, such as a named shopkeeper, is simply a template that is never copied.
- **Copy:** an NPC whose `template` points to the template it was made from: "Goblin 1", "Goblin 2"… Each copy is an independent character with its own HP, AP, inventory, effects and campaigns.
- **Making a copy** duplicates the template's stats, race and subrace, class and subclass, abilities, spells, inventory and active effects. Item copies (individual weapons and armor) are copied again from the same item template, so each NPC copy has its own; item templates carried directly (gold, potions…) are shared. Campaigns are not copied: the copy is added to the campaigns it is made for.
- Later changes to a template do not change the copies already made.
- Deleting a template also deletes all its copies.
- Deleting a copy also deletes the item copies in its inventory (its individual weapons and armor). Item templates carried directly (gold, potions…) are shared with other characters, so they are kept; so is any item another character also holds.
- An NPC with a unique ability cannot have copies (see game rules).

### Race

| Field | Type | Notes |
|---|---|---|
| name | string | |
| description | text | |
| speed | int | In meters |

- Many Characters belong to (*pertenece a*) a Race.
- Grants (*otorga*) Abilities through RaceAbility.
- Has (*tiene*) many Subraces.

### Subrace

| Field | Type | Notes |
|---|---|---|
| name | string | |
| description | text | |
| race_id | FK → Race | Required. The race this subrace belongs to |

- Belongs to one Race (*tiene*); a Race has many Subraces. For example, "Elfo de los bosques" is a subrace of "Elfo".
- Many Characters belong to (*pertenece a*) a Subrace. A character's subrace is optional, but must be a subrace of its race (see game rules).
- Grants (*otorga*) Abilities through SubraceAbility.
- Has no speed of its own: speed comes from the race.

### CharacterClass

| Field | Type | Notes |
|---|---|---|
| name | string | |
| primary_ability | string | |
| description | text | |
| magic | string | `completa` (full caster), `media` (half caster), `pacto` (pact magic) or `ninguna` (default). Gives the magic points at each level (rule 15) |

- Defines (*define*) many Characters.
- Grants (*otorga*) Abilities through ClassAbility.
- Can cast (*puede lanzar*) many Spells. A Spell can be available to many classes.
- Has (*tiene*) many Subclasses.

### Subclass

| Field | Type | Notes |
|---|---|---|
| name | string | |
| description | text | |
| char_class_id | FK → CharacterClass | Required. The class this subclass belongs to |

- Belongs to one CharacterClass (*tiene*); a class has many Subclasses. For example, "Círculo de la Luna" is a subclass of "Druida".
- Defines (*define*) many Characters. A character's subclass is optional, but must be a subclass of its class (see game rules).
- Grants (*otorga*) Abilities through SubclassAbility.

---

## Abilities

### Ability

| Field | Type | Notes |
|---|---|---|
| name | string | |
| unique | bool | If true, only one character per campaign can have it. Not exclusive for characters without a campaign (see game rules). |
| description | text | |
| feat | bool | Default false. A feat (*dote*): chosen when levelling up at levels 4, 8, 12, 16 and 20 instead of the ability score improvement (rule 16) |
| feat_min_level | int | Default 4. Only for feats: the lowest level at which it can be chosen (19 for epic boons, so only at level 20) |

- Applies (*aplica*) many Effects.

### CharacterAbility (link table)

| Field | Type | Notes |
|---|---|---|
| character_id | FK → Character | |
| ability_id | FK → Ability | |
| max_uses | int | **Optional.** Empty: no limited uses |
| uses_left | int | Up to `max_uses` |
| recharge | string | `corto`, `largo` or `ninguno`: which rest gives the uses back |
| cooldown_rounds | int | **Optional.** Empty: no cooldown |
| cooldown_left | int | Up to `cooldown_rounds` |

The abilities a character holds directly (*posee*), with their uses. The master sets the uses and cooldowns: they are not read from the SRD text. «Usar» spends a use and starts the cooldown; a long rest gives back every use, a short rest only those with `recharge = corto`, and both end the cooldowns. In combat, the end of each round takes 1 from `cooldown_left`. Abilities granted by race, subrace, class or subclass appear on the sheet as traits; give the character a row here too if one has uses.

### UniqueSkill

A character's unique skill (*habilidad única*), one per character, with its approval (game rule 17).

| Field | Type | Notes |
|---|---|---|
| character_id | FK → Character | One-to-one. Deleting the character deletes it |
| name | string | |
| description | text | What it does |
| status | string | `pendiente` (default), `aprobada` or `rechazada` |
| master_note | text | **Optional.** The master's note to the player: why it was rejected or changed |
| ability_id | FK → Ability | **Optional.** One-to-one. The unique Ability created on approval; set only when approved (`CheckConstraint`). Deleting the ability deletes this row |
| updated_at | datetime | |

### RaceAbility (link table)

| Field | Type | Notes |
|---|---|---|
| race_id | FK → Race | |
| ability_id | FK → Ability | |
| level_required | int | Level a character of this race needs to get the ability |

### ClassAbility (link table)

| Field | Type | Notes |
|---|---|---|
| char_class_id | FK → CharacterClass | |
| ability_id | FK → Ability | |
| level_required | int | Level a character of this class needs to get the ability |

### SubraceAbility (link table)

| Field | Type | Notes |
|---|---|---|
| subrace_id | FK → Subrace | |
| ability_id | FK → Ability | |
| level_required | int | Level a character of this subrace needs to get the ability |

### SubclassAbility (link table)

| Field | Type | Notes |
|---|---|---|
| subclass_id | FK → Subclass | |
| ability_id | FK → Ability | |
| level_required | int | Level a character of this subclass needs to get the ability |

Race, subrace, class and subclass abilities are kept in four tables on purpose: every foreign key is real and never empty. A single table would need always-empty columns or a foreign key the database cannot check. Abilities from all four follow the same rules (see game rules 6 and 7).

---

## Spells and combat

### Spell

| Field | Type | Notes |
|---|---|---|
| name | string | |
| level | int | 0 = cantrip (*truco*) |
| casting_time | string | Up to 200 characters: reactions describe their trigger, e.g. "Reacción, que llevas a cabo cuando…" |
| range | int | **Optional.** In meters; 0 = Lanzador (self) or Toque. Empty when the range is not a distance: Vista, Ilimitado or Especial |
| duration | string | |
| description | text | |

- Requires (*requiere*) many Components.
- Performs (*realiza*) many Attacks. A spell's damage, dice and armor damage come from its Attacks.
- Applies (*aplica*) many Effects.

### Component

| Field | Type | Notes |
|---|---|---|
| type | string | |

- Required by many Spells.

### Attack

| Field | Type | Notes |
|---|---|---|
| name | string | |
| extra | int | Fixed damage that does not depend on dice |
| range | int | **Optional.** In meters; in melee, 1 square = 1 m. Empty when the attack has no set range (bombs) |
| area | int | In meters |
| cooldown | int | In rounds |
| stat_bonus | string | **Optional.** `str` (Fuerza) or `dex` (Destreza): the ability whose modifier is added to the damage. Some blunt attacks add Strength |

Damage = base dice + `extra` + the `stat_bonus` modifier, if any + dice per extra level × extra levels (see game rule 4).

- Performed by Weapons and by Spells (*realiza*, many-to-many in both cases).
- Rolls (*tira*) many DiceRolls. One attack can combine several dice rolls of different kinds.
- Inflicts (*inflige*) ArmorDamages (many-to-many). Most attacks have none; see ArmorDamage for the four cases.
- Applies (*aplica*) many Effects.

### DiceRoll

| Field | Type | Notes |
|---|---|---|
| amount | int | Number of dice |
| sides | int | Sides per die. Amount 2 and sides 6 means 2d6. |
| per_extra_level | bool | Default false. If true, these dice are rolled once per extra level (see game rule 4) |

For example, Marchitar's attack has 8d8 (`per_extra_level = false`) and 1d8 (`per_extra_level = true`): cast with a level 6 slot, it deals 8d8 + 2 × 1d8.

### ArmorDamage

| Field | Type | Notes |
|---|---|---|
| type | string | `penetrating` (Penetrante) or `shredding` (Triturante) |
| amount | int | Number of AP points affected |

An attack is in one of four armor-damage cases. Only two of them are values of `type`; the other two come from how many ArmorDamages are linked to the attack:

- **None:** the attack has no ArmorDamage linked. Not a value of `type`. This is why armor damage is its own table: attacks without it carry no empty columns.
- **Penetrante** (`penetrating`): the damage ignores `amount` AP points. It does not change the AP.
- **Triturante** (`shredding`): the attack subtracts `amount` AP points after dealing damage. Until then the AP are unchanged.
- **Both:** the attack has more than one ArmorDamage linked, a penetrante one and a triturante one. Not a value of `type` either. There is no order to choose: the damage is dealt against the unchanged AP, ignoring the penetrante amount, and the triturante amount is subtracted afterwards (see game rule 3).

---

## Effects

### Effect

| Field | Type | Notes |
|---|---|---|
| name | string | |
| description | text | |
| duration | string | **Optional.** Conditions such as Agarrado or Cegado have no duration of their own |

Applied (*aplica*, many-to-many) by Spells, Abilities, Attacks, Items and ArmorTypes. Weapon and armor effects are attached through their Item, in ItemEffect, which also stores each effect's value. The weapon traits (BARRIDO, SANGRADO, EMPUJE…) are Effects.

### ActiveEffect (link table)

| Field | Type | Notes |
|---|---|---|
| character_id | FK → Character | |
| effect_id | FK → Effect | |
| remaining_turns | int | |

The effects currently on a character. In combat, the end of each round takes 1 from `remaining_turns`, and the effects that reach 0 are removed.

---

## Items and equipment

### Item

| Field | Type | Notes |
|---|---|---|
| name | string | |
| price | int | **Optional.** The sample data has no prices |
| description | text | |
| consumable | bool | |
| template_id | FK → Item | **Optional.** Only for copies: the item template it was made from |

- Gold is an Item; how much a character has is the quantity in Inventory.
- Deleting a character does not delete its items, except for NPC copies (see NPCs).
- A Weapon or an Armor *is an* (*es un*) Item: one-to-one.
- Applies (*aplica*) many Effects through ItemEffect, each with an optional value.

### Item templates and copies

Items work with templates and copies (*es copia de*, Item → Item), like NPCs. The template field lives only on Item: a Weapon or an Armor follows the Item it belongs to, so one field covers all three.

- **Template:** an item with no `template`, for example "Espada larga" or "Oro". It is a catalog entry, defined once. A template's Weapon or Armor row describes a kind of weapon or armor.
- **Copy:** an item whose `template` points to the template it was made from. It is one physical piece with its own Weapon or Armor row, so equipping or upgrading it changes only that piece.
- **Making a copy** duplicates the item's data, effects (with their values) and Weapon or Armor row, including its attacks. A copy made from another copy points to the same template. The new copy starts unequipped, since no character carries it yet; copying an NPC keeps what the template has equipped, because the NPC copy carries its items right away.
- **Weapons and armor are carried as copies:** characters never carry a weapon or armor template. Generic items (gold, potions…) are templates carried directly; how many a character has is the quantity in Inventory.
- A template cannot be deleted while it has copies: delete the copies first.

### ItemEffect (link table)

| Field | Type | Notes |
|---|---|---|
| item_id | FK → Item | |
| effect_id | FK → Effect | |
| value | int | **Optional.** The trait's X: SANGRADO 3, EMPUJE 2… Empty if the trait has no value (BARRIDO) |

One row per item and effect. The Effect's description says what X means; for example, SANGRADO 3 makes the target lose 3 HP per round.

### Inventory (link table)

| Field | Type | Notes |
|---|---|---|
| character_id | FK → Character | |
| item_id | FK → Item | |
| quantity | int | |

One row per character and item. Weapons and armor in an inventory are always copies (see Item templates and copies).

### Weapon

| Field | Type | Notes |
|---|---|---|
| item_id | FK → Item | One-to-one |
| to_hit | string | A formula: an optional integer, an ability (`str` or `dex`) and an optional multiplier (see game rule 14) |
| equipped | bool | Only copies carried by a character can be equipped (see game rule 5) |

Examples of `to_hit`: `dex` (the Dexterity modifier), `2 + str` (2 + the Strength modifier), `-3 + dex x2` (−3 + 2 × the Dexterity modifier). The revolver's one-hand to hit is in its item's description.

A template's Weapon row describes a **kind** of weapon; a copy's Weapon row is an **individual** weapon (see Item templates and copies).

- Performs (*realiza*) many Attacks.

### Armor

| Field | Type | Notes |
|---|---|---|
| item_id | FK → Item | One-to-one |
| armor_type_id | FK → ArmorType | Required |
| ap | int | This piece's own AP; raised when the armor is upgraded |
| magic_ap | int | This piece's own magic AP |
| equipped | bool | Only copies carried by a character can be equipped, one armor per character (see game rule 5) |

A template's Armor row describes a **kind** of armor; a copy's Armor row is an **individual piece**, so upgrading it changes only that piece (see Item templates and copies).

### ArmorType

| Field | Type | Notes |
|---|---|---|
| type | string | |
| ap | int | Added to the AP of every armor of this type |
| magic_ap | int | Added to the magic AP of every armor of this type |

- Has many Armors (*es de tipo*). Each Armor has exactly one ArmorType.
- Applies (*aplica*) many Effects.

---

## Campaign contents (E4)

Everything that belongs to one campaign: its diary, its world wiki, its board, its role-play scenes, its maps and its fights. They live in `CampaignManager/campaign_models.py`. Every one of them reaches its campaign through a foreign key (directly, or through its scene, quest, map or encounter), and all of them are deleted with it (`CASCADE`).

**Hidden content is filtered on the server:** each model with something hidden has a `visible_for(user, campaign)` queryset that returns everything to the campaign's masters and only the visible rows to its players. Templates never hide anything with CSS.

### Session (diary chronicle)

| Field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| number | int | Unique per campaign |
| title | string | |
| played_on | date | Real date the session was played |
| world_day | string | **Optional.** Day in the world |
| body | text | The chronicle. `[[Name]]` links to a world page or a player character |
| xp_awarded | int | |
| loot | string | **Optional.** |
| master_notes | text | **Optional.** Only masters see them |
| published | bool | Players only see published sessions |

### TimelineEvent

| Field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| session_id | FK → Session | **Optional.** `SET_NULL` |
| title | string | |
| description | text | |
| world_day | string | **Optional.** |
| kind | string | `combate`, `social`, `descubrimiento`, `viaje`, `giro`, `mision` or `secreto` |
| location_id | FK → MapLocation | **Optional.** `SET_NULL`. Must be on a map of the same campaign |
| hidden | bool | Hidden events are not sent to players |

- Many-to-many with the Characters who took part (`characters`). Closing a scene adds an event «Escena: …».

### Page (world wiki)

| Field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| title | string | |
| slug | slug | Unique per campaign, made from the title |
| category | string | `lugar`, `pnj`, `faccion`, `objeto` or `historia` |
| parent_id | FK → Page | **Optional.** `SET_NULL`. Only a place or a faction of the same campaign, never forming a cycle |
| summary | text | Shown in the floating preview of `[[links]]` |
| body | text | `[[Name]]` links to another page |
| secret | text | Only masters see it |
| image | image | **Optional.** (`mundo/`) |
| revealed | bool | A page that is not revealed does not exist for players: 404, no preview, its links become plain text |
| location_id | One-to-one → MapLocation | **Optional.** `SET_NULL`. Only for places. Revealing or hiding the page moves its location between `oculto` and `conocido`, and the other way round |

### Quest and QuestObjective

| Quest field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| title | string | |
| description | text | |
| status | string | `activa`, `completada` or `fallida` |
| giver_id | FK → Page | **Optional.** `SET_NULL`. Who gave it |

| QuestObjective field | Type | Notes |
|---|---|---|
| quest_id | FK → Quest | |
| text | string | |
| done | bool | |
| hidden | bool | Players see «Objetivo oculto», never the text. Ticking it reveals it (`hidden` becomes false) |
| order | int | |

### BoardCard and BoardLink (the board)

| BoardCard field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| title | string | |
| text | text | |
| x, y | int | Position on a 1100 × 640 board, so a 200 × 120 note fits |
| page_id | FK → Page | **Optional.** `SET_NULL` |
| classified | bool | Players see «CLASIFICADO» and not its title or text until it is revealed |
| revealed | bool | |
| author_id | FK → User | **Optional.** `SET_NULL`. Players can remove only their own notes |

BoardLink joins two notes with red thread (`card_a`, `card_b`, both `CASCADE`). The pair is stored in order (`card_a` < `card_b`), so a thread is never stored twice.

### Scene and Post (role-play scenes)

| Scene field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| title | string | |
| location_id | FK → Page | **Optional.** `SET_NULL` |
| is_open | bool | Closed scenes cannot get messages |

- Many-to-many with the participating Characters (`participants`).

| Post field | Type | Notes |
|---|---|---|
| scene_id | FK → Scene | |
| author_id | FK → User | `SET_NULL` |
| as_character_id | FK → Character | **Optional.** `SET_NULL`. Empty: the narrator |
| text | text | |
| roll | string | Result of «/tirar 2d6+3», rolled on the server |
| whisper_to_id | FK → Character | **Optional.** `SET_NULL`. Only the author, this character's player and the masters see it. A player's whisper goes to the masters |
| created_at | datetime | |

### Map, MapLocation and PartyMove

| Map field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| name | string | |
| image | image | **Optional.** (`mapas/`) |
| order | int | |

| MapLocation field | Type | Notes |
|---|---|---|
| map_id | FK → Map | |
| name | string | |
| x, y | float | Position in % of the image (0 to 100) |
| status | string | `oculto` (not sent to players), `conocido` or `visitado` |
| importance | string | `principal` (capitals and key places, drawn bigger) or `secundario`. The map can hide the secondary places, or all of them |

PartyMove is one step of the party's route: `map`, `location` (both `CASCADE`), `session` (optional, `SET_NULL`) and `order`. Moving the party marks the location as `visitado`. The map's session slider shows the route up to a session.

### WeatherRegion (the map's weather layer)

A region of a map linked to a real place, whose live weather the map shows (rule 20).

| Field | Type | Notes |
|---|---|---|
| map_id | FK → Map | `CASCADE` |
| name | string | |
| location_id | FK → MapLocation | **Optional.** `CASCADE`. The region is a circle around this location's pin… |
| points | JSON | …or a polygon drawn by the master: `[[x, y], ...]` in % of the image, at least 3 points (checked in `clean()`) |
| place_name | string | The real place, e.g. «Bilbao, País Vasco, España» |
| latitude, longitude | float | −90..90 and −180..180 |
| weather | JSON | **Optional.** The last weather fetched: temperature, is_day, WMO code, wind, precipitation |
| weather_at | datetime | **Optional.** When it was fetched |

### RumourCategory and Rumour («Rumores»)

| RumourCategory field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| name | string | Unique in its campaign |
| color | string | `#RRGGBB` (validator) |
| order | int | |

| Rumour field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| category_id | FK → RumourCategory | **Optional.** `SET_NULL`: deleting a category leaves its rumours without one |
| text | text | `[[Name]]` links to a world page |
| source_page_id | FK → Page | **Optional.** Who said it: an NPC or faction of the world… |
| source | string | **Optional.** …or free text («Un pregonero») |
| place_id | FK → Page | **Optional.** Where the party heard it: a place of the world |
| session_id | FK → Session | **Optional.** When |
| truth | string | `sin_decidir` (default), `verdad`, `medias` or `mentira`. Only masters see it |
| published | bool | Players see only published rumours (`visible_for()`) |
| created_at | datetime | Newest first |

---

### Encounter and Combatant (combat turns)

| Encounter field | Type | Notes |
|---|---|---|
| campaign_id | FK → Campaign | |
| active | bool | At most one active encounter per campaign (`UniqueConstraint` with a condition) |
| round | int | |
| turn_index | int | Position of the combatant whose turn it is |

| Combatant field | Type | Notes |
|---|---|---|
| encounter_id | FK → Encounter | |
| character_id | FK → Character | **Optional.** `CASCADE`. Characters keep their hit points and AP on their sheet |
| name | string | For enemies without a character. A combatant has a character or a name (`CheckConstraint`) |
| initiative | int | **Optional.** Typed by the master |
| hp, hp_max | int | **Optional.** Only for enemies without a character |
| order | int | Turn order; «Ordenar por iniciativa» rewrites it |

Players see the order and whose turn it is; they never receive the hit points of enemies or NPCs. When «Siguiente» closes a round, cooldowns and active effects of the characters in the fight lose 1 round.

---

## Initial data

The database starts from three sources, loaded in this order on a database with empty tables. For E4 the whole demo loads with one command:

```bash
python manage.py migrate
python manage.py cargar_demo    # datos_iniciales_srd + armas_caseras + demo_campana + demo images
```

`demo_campana.json` holds the prototype's campaigns (La Corona de Ceniza, Ecos de Kioto, Prólogo en Puerto Gris), its users, armor types and armors, characters, NPC templates and copies, and at least one row of every campaign model. It is generated by `python manage.py generar_demo --output CampaignManager/fixtures/demo_campana.json` on a database with only the two rules fixtures. Users and groups go by natural key (`["carlos"]`, `["Campañas"]`), because the groups come from a migration.

The E3 way, without the demo, still works:

```bash
python manage.py migrate
python manage.py loaddata datos_iniciales_srd   # the rules catalog (SRD 5.2.1 in Spanish), without weapons
python manage.py loaddata armas_caseras         # our homemade weapons
python manage.py populate                       # the sample campaign on top of them
```

- **`CampaignManager/fixtures/datos_iniciales_srd.json`**: the official SRD 5.2.1 in Spanish, adapted to this model. It holds 9 races and 24 subraces, 12 classes and 12 subclasses, 286 abilities, 339 spells, 97 attacks (all of them from spells), 86 items (item templates without a price) and the 15 conditions as Effects. It has no weapons, armor or armor types. Its primary keys are fixed and start at 1, so it must be loaded into empty tables: rows with the same ids would be overwritten. Distances already follow rule 10 (6 ft = 2 m).
- **`CampaignManager/fixtures/armas_caseras.json`**: our homemade weapons, which replace the SRD's. It holds 32 weapon item templates (melee weapons, shields, bows, firearms and bombs) with their Weapon rows and 99 attacks, the 23 weapon traits as Effects (BARRIDO, SANGRADO…), 52 ItemEffects with the value of each trait, 12 ArmorDamages and 2 DiceRolls. Its primary keys start above the SRD's, so it overwrites none of them.
- **`python manage.py populate`**: adds the test user, the Alforge campaign and Balasar, linked to the SRD rows by name. It also adds what neither fixture includes: the druid subclass Círculo de la Luna, the spells Escudo de piedra and Tierra en erupción, a "Dardo" weapon template (the homemade weapons have no dart), the other traits on Balasar's sheet and his items. It stops with an error if either fixture is not loaded, and it can be run again without duplicating anything.
- **The order matters:** `populate` creates rows with the next free ids, the same ones the fixtures use, so a fixture loaded after `populate` would overwrite them. To load the fixtures again on a database that already has data, start again from empty tables.
- **License:** the SRD is published under CC-BY-4.0, which requires its attribution statement on the website. It is in the footer of `base.html`, on every page.

---

## Game rules the code must enforce

These are house rules or constraints the diagram cannot express.

1. **Hit points:** `hp` is the maximum and `temp_hp` is the current value.
2. **Armor points:** AP and AC are separate stats. Magic AP protects against magic damage.
   - **Maximum AP is not stored on the character.** It is calculated for the character sheet from the equipped armor: `Armor.ap + ArmorType.ap`, and `Armor.magic_ap + ArmorType.magic_ap` for magic AP. If no armor is equipped, both maximums are 0.
   - The two parts are stored separately: `Armor.ap` is that piece's own AP, already including its upgrades, and `ArmorType.ap` is the amount every armor of that type adds.
   - Only current AP is stored on the character, as `temp_ap`, because it changes during combat. Magic AP has no stored current value: only its calculated maximum exists.
3. **Armor damage:** the AP only change after the damage is dealt.
   - **Penetrante:** the damage ignores `amount` AP. It never changes the AP.
   - **Triturante:** subtracts `amount` AP, always after the damage is dealt.
   - **Both:** no order is needed. The damage is dealt against the unchanged AP, ignoring the penetrante amount; then the triturante amount is subtracted.
4. **Attack damage:** the sum of the attack's dice with `per_extra_level = false`, plus `extra`, plus the modifier of its `stat_bonus` ability if it has one, plus its dice with `per_extra_level = true` rolled once per extra level.
   - **Ability modifier:** (score − 10) / 2, rounded down. For example, Strength 8 → −1 and Dexterity 14 → +2. `Character.modifier("str")` works it out.
   - **In code:** `Attack.damage(character, extra_levels=0)` returns the dice, with dice of the same sides added together, and the fixed bonus (`extra` + the modifier); `Attack.damage_text()` writes it as "2d10 + 8". The character is needed only when the attack has a `stat_bonus`.
   - **Spells of level 1 or higher:** extra levels = level of the spell slot used − `Spell.level`.
   - **Cantrips** (`Spell.level = 0`): extra levels = how many of the character levels 5, 11 and 17 the character has reached (0 to 3).
   - **Weapons:** dice with `per_extra_level = true` are not used.
5. **Equipment:**
   - A character can have several weapons equipped but only **one armor** equipped.
   - A character can only equip weapons and armor that are in their Inventory.
   - Armor and Weapon have no character column, because ownership goes through Inventory → Item. So these rules are checked in code, not by a database constraint:
     - `Weapon.clean()` and `Armor.clean()`: an equipped piece must be a copy that a character carries. An armor cannot be equipped if a character carrying it already has another armor equipped.
     - `Inventory.clean()`: a character wearing an armor cannot pick up another armor that is equipped.
     - When an item leaves the last inventory that held it, it is unequipped.
     - A new item copy starts unequipped (see Item templates and copies).
6. **Unique abilities:** an Ability with `unique = true` can belong to only one character per campaign. Characters in different campaigns can each have it.
   - A character in several campaigns must be the only one with that ability in each of them.
   - A character **cannot join** a campaign where another character already has one of its unique abilities.
   - A character cannot get a unique ability that another character already has in any of its campaigns.
   - Characters without a campaign keep their unique abilities, which stay marked as unique, but are not exclusive in any sense: they block no one and nothing blocks them, until they join a campaign.
   - It applies to abilities from any source: race, subrace, class, subclass or held directly. A granted ability counts once the character's level reaches its `level_required` (rule 7).
   - Campaign membership and abilities are many-to-many links, so this is checked in code, not by a database constraint:
     - The character's admin form, on the campaigns, abilities, race, subrace, class and subclass being saved, and `Character.clean()` on the saved ones.
     - Adding campaigns or abilities in code (`character.campaigns.add()`, `campaign.characters.add()`, `character.abilities.add()`, `ability.character_set.add()`) raises a `ValidationError`, including when several characters joining together share a unique ability.
     - `Ability.clean()`: an ability cannot be made unique while two characters in a campaign have it.
     - The `clean()` of RaceAbility, SubraceAbility, ClassAbility and SubclassAbility: a race, subrace, class or subclass cannot grant a unique ability that would leave two characters in a campaign with it.
7. **Ability levels:** a character gets a race, subrace, class or subclass ability only once their level is at least `level_required`.
8. **One class per character:** no multiclassing. A character has at most one subclass and at most one subrace.
9. **Calculated, not stored:** speed comes from the race (subraces have no speed); maximum AP and magic AP come from the equipped armor; initiative and passive perception come from the ability scores. The level is stored (rule 16); the level the XP is enough for is calculated with the SRD table (`rules.XP_THRESHOLDS`).
10. **Units:** distances (race speed, spell and attack range, attack area) are in meters; in melee, 1 square = 1 m. Attack cooldown is in rounds. A spell's or attack's range is empty when it is not a distance. Distances in feet (D&D books, character sheets) are converted with 6 ft = 2 m, rounded to the nearest meter: 30 ft = 10 m, 35 ft = 12 m, 25 ft = 8 m.
11. **NPCs:**
   - Only NPCs can have a template (checked by a database constraint).
   - A copy's template must be a template: an NPC that is not a copy. A character cannot be its own template, and an NPC that already has copies cannot become a copy.
   - Only templates can be copied.
   - An NPC with a unique ability cannot have copies. So a template with a unique ability cannot be copied, a template that has copies cannot get a unique ability, and an ability held by a template with copies cannot be made unique.
   - Deleting an NPC copy deletes the item copies in its inventory (its individual weapons and armor), unless another character also holds them. Item templates are never deleted. Deleting a player character or an NPC template deletes no items. This runs on every delete of a copy: directly, through its template, or in bulk from the admin.
12. **Item templates:**
   - A copy's template must be a template: an item that is not a copy. An item cannot be its own template, and an item that already has copies cannot become a copy.
   - Weapons and armor are carried as copies: an Inventory row cannot hold a weapon or armor template.
   - A template cannot be equipped; only its copies can.
   - A template cannot be deleted while it has copies (checked by the database).
13. **Subraces and subclasses:**
   - A character's subrace must belong to its race (`character.subrace.race` = `character.race`), and its subclass to its class (`character.subclass.char_class` = `character.char_class`).
   - The database cannot check this across two foreign keys, so `Character.clean()` does; the admin form shows both errors at once.
   - Copying an NPC copies its subrace and subclass too.
14. **Weapon to hit:** `Weapon.to_hit` is a formula with an optional integer, an ability (`str` or `dex`) and an optional multiplier for that ability's modifier: `-3 + dex x2` = −3 + 2 × the Dexterity modifier (see rule 4 for modifiers).
   - The format is checked by a validator when the weapon is validated (`full_clean()`, the admin). Loading a fixture does not run validators.
   - **In code:** `Weapon.to_hit_bonus(character)` works it out, and `parse_to_hit()` splits a formula into its integer, ability and multiplier. The character page shows the to hit and damage of every weapon the character carries.
15. **Open decisions (E4), kept in `CampaignManager/rules.py`:**
   - **XP → level:** the SRD table, `XP_THRESHOLDS`.
   - **Magic points:** casting with a level N slot costs N points (`magic_point_cost`); cantrips cost nothing. The highest slot is `ceil(level / 2)`, at most 9 (`max_spell_slot`).
   - **Maximum magic points (house rule):** given by the class and the level (`class_magic_points`): as many points as levels of the spell slots the SRD 5.2.1 gives that class, so the points are worth the same as the slots. A full caster uses the full caster table (level 1: 2, level 5: 16, level 20: 89); a half caster, that table at half its level rounded up; pact magic, the warlock's slots × their level; `ninguna`, 0. The master adds or takes away points per character (`Character.magic_points_bonus`).
   - **Ability uses:** set by the master in CharacterAbility, not read from the SRD.
   - **Character creation:** standard array (15, 14, 13, 12, 10, 8) or point buy (27 points, 8 to 15, SRD costs). Level 1 hit points = the class hit die's maximum + the Constitution modifier; armor class = 10 + the Dexterity modifier.
16. **Experience and levels:** only masters give or take XP (from the sheet, or to several party members at once). XP never changes the level by itself: once `xp` reaches the next level of the table, the player (or a master) levels up from the sheet, one level at a time, with that level's choices, which depend on the campaign's ruleset (rule 18):
   - **Hit points:** in Aegis, 1d4 + the Constitution modifier, at least 1, whatever the class (`LEVEL_UP_HIT_DIE`); the die is rolled at the table in front of the master and the player types the result (1 to 4). In the SRD, the class hit die (read from its description): its fixed value (half + 1) or rolled by the server, + the Constitution modifier, at least 1; if the class does not say its die, the player types the hit points. Current hit points go up the same. If the Constitution modifier changes, the levels already had count it too.
   - **Subclass:** chosen on reaching level 3 (`SUBCLASS_LEVEL`) if the character has none.
   - **Ability score improvement or feat:** in Aegis every 4 levels from level 4 (`FEAT_LEVELS`: 4, 8, 12, 16 and 20), for every class; in the SRD at 4, 8, 12, 16 and 19 (the epic boon), plus 6 and 14 for the Guerrero and 10 for the Pícaro (`SRD_FEAT_LEVELS`, `SRD_EXTRA_FEAT_LEVELS`). Either two +1, to two scores or both to one, with no score above 20; or a feat: an Ability with `feat = true` and `feat_min_level` up to the new level that the character does not have yet. The feat is added to its abilities (CharacterAbility).
   - **Magic points:** the maximum follows the new level (rule 15) and the current points go up the same.
   - **Spells:** the master can always give (or take away) spells from the sheet, when the character buys or learns one in the story. In Aegis that is the only way. In the SRD the player also picks new spells from the class list when levelling up, as many as the SRD 5.2.1 class table adds at that level (`srd_new_spells`: prepared spells, and one more cantrip at levels 4 and 10), up to the highest slot the class has at that level (`highest_slot`).
   - The new level, subclass or feat can grant a unique ability: rule 6 is checked before saving, and nothing changes if it fails. The level-up form says which level it is for, so sending it twice does not go up two levels.
17. **Unique skill (Aegis only):** each character can have one unique skill (UniqueSkill). In SRD campaigns the creator skips its step and the sheet, the list and the menu do not show it; an approved one stays as a unique ability of the character.
   - The player describes it (name and what it does) in the character creator, in an optional step, or later from the «Rasgos» tab of the sheet if the character has none.
   - It waits for a master (`pendiente`). Masters see a counter on «Personajes» in the menu (and on the folded menu's button on the phone) and review it there or on the sheet: they approve it as it is, change it before approving it, or reject it, with an optional note the player sees. A rejected skill can be rewritten and sent again. When a master writes it, it is approved at once.
   - Approving it creates an Ability with `unique = true` and gives it to the character (CharacterAbility), so rule 6 applies and the master can give it uses or a cooldown. The sheet shows it at the top of «Rasgos», highlighted.
   - Until it is approved, only the character's player and the masters see it.
18. **Rulesets:** each campaign plays with **Aegis**, our variant of the SRD 5.2.1 with our additions, or with the **standard SRD 5.2.1** (`Campaign.ruleset`, `rules.AEGIS` / `rules.SRD`). They differ only in levelling up (rule 16: hit points, the levels of the improvement or feat, spells) and in unique skills (rule 17). The rest is the same in both: XP and stored level, subclass at level 3, magic points by class and level, the master's spells, the AP and armor damage rules and the homemade weapons. A new campaign uses Aegis; changing it applies from the next level up on.
19. **Rumours («Rumores de <campaign>»):** only masters write, edit, publish and delete them. Players see the published ones with their category (name and colour), never their truth; who said them and where are links only if that page of the world is revealed to them, and the session only if its chronicle is published. Each campaign has its own categories, which its masters create, rename, recolour and delete; a new campaign starts with «Rumor», «Comunicado oficial», «Noticia» and «Leyenda» (a `post_save` signal; migration 0030 gives them to existing campaigns).
20. **Weather layer:** each region of a map can be linked to a real place; «Tiempo real» shows that place's live weather on the region (condition, temperature, and wind from 20 km/h and rain when there is any).
   - **Source:** Open-Meteo (free, no key), fetched on the server (`weather.py`), all the stale regions of a map in one request. Each region keeps its last values for 20 minutes (`WEATHER_TTL`): pages within that time do not call the API.
   - **API down:** the regions keep their last values, marked as old; a region never fetched shows nothing. The API is not called again for 5 minutes.
   - **One clock:** the time shown is the campaign's (`Campaign.time_zone`), never each place's. **Day and night** follow each real place (`is_day`): a region where it is night is shaded and shows a moon, so it can be day in one region and night in another at the same hour.
   - **Who:** only masters link and unlink regions (a location of the map, or a polygon they draw) and search real places; players see just the layer, without the real places, and not the regions of locations still hidden from them.

---

## Django mapping

### Where to declare each relationship

| Diagram relationship | Django field |
|---|---|
| *dirige* (User ↔ Campaign) | `Campaign.masters = ManyToManyField(User, related_name="dm_campaigns")` |
| *juega en* (User ↔ Campaign) | `Campaign.players = ManyToManyField(User, related_name="player_campaigns")` |
| *controla* | `Character.player = ForeignKey(User, null=True, blank=True)` |
| *contiene* | `Character.campaigns = ManyToManyField(Campaign, blank=True, related_name="characters")` |
| *pertenece a* | `Character.race = ForeignKey(Race)` |
| *pertenece a* (subrace) | `Character.subrace = ForeignKey(Subrace, null=True, blank=True)` |
| *define* | `Character.char_class = ForeignKey(CharacterClass)` |
| *define* (subclass) | `Character.subclass = ForeignKey(Subclass, null=True, blank=True)` |
| *tiene* (race) | `Subrace.race = ForeignKey(Race, related_name="subraces")` |
| *tiene* (class) | `Subclass.char_class = ForeignKey(CharacterClass, related_name="subclasses")` |
| *posee* | `Character.abilities = ManyToManyField(Ability, through="CharacterAbility")` |
| *conoce* | `Character.spells = ManyToManyField(Spell)` |
| *lleva* | `Character.items = ManyToManyField(Item, through="Inventory")` |
| *sufre* | `Character.effects = ManyToManyField(Effect, through="ActiveEffect")` |
| *otorga* (race) | `Race.abilities = ManyToManyField(Ability, through="RaceAbility")` |
| *otorga* (class) | `CharacterClass.abilities = ManyToManyField(Ability, through="ClassAbility")` |
| *otorga* (subrace) | `Subrace.abilities = ManyToManyField(Ability, through="SubraceAbility")` |
| *otorga* (subclass) | `Subclass.abilities = ManyToManyField(Ability, through="SubclassAbility")` |
| *puede lanzar* | `CharacterClass.spells = ManyToManyField(Spell)` |
| *requiere* | `Spell.components = ManyToManyField(Component)` |
| *realiza* (spell) | `Spell.attacks = ManyToManyField(Attack)` |
| *realiza* (weapon) | `Weapon.attacks = ManyToManyField(Attack)` |
| *tira* | `Attack.dice = ManyToManyField(DiceRoll)` |
| *inflige* | `Attack.armor_damages = ManyToManyField(ArmorDamage)` |
| *es un* (weapon) | `Weapon.item = OneToOneField(Item)` |
| *es un* (armor) | `Armor.item = OneToOneField(Item)` |
| *es de tipo* | `Armor.armor_type = ForeignKey(ArmorType)` |
| *es copia de* (NPCs) | `Character.template = ForeignKey("self", null=True, blank=True, related_name="copies")` |
| *es copia de* (items) | `Item.template = ForeignKey("self", null=True, blank=True, related_name="copies")` |
| *aplica* | `effects = ManyToManyField(Effect)` on Spell, Ability, Attack and ArmorType |
| *aplica* (items) | `Item.effects = ManyToManyField(Effect, through="ItemEffect")` |

The two User ↔ Campaign relationships need different `related_name` values, otherwise Django reports a clash. No other relationship needs one; `Character.campaigns` uses `related_name="characters"`, `Character.template` and `Item.template` use `related_name="copies"`, and `Subrace.race` and `Subclass.char_class` use `related_name="subraces"` and `"subclasses"`, only so they read as `campaign.characters`, `template.copies`, `race.subraces` and `char_class.subclasses`.

### Constraints and choices

- `UniqueConstraint` on Inventory (`character`, `item`), ItemEffect (`item`, `effect`), CharacterAbility (`character`, `ability`), RaceAbility (`race`, `ability`), SubraceAbility (`subrace`, `ability`), ClassAbility (`char_class`, `ability`) and SubclassAbility (`subclass`, `ability`).
- `CheckConstraint` on Character: `template` is empty or `is_npc` is true; death saves up to 3. `magic_points` up to the calculated maximum is checked in `Character.clean()`.
- `CheckConstraint` on CharacterAbility: `uses_left` up to `max_uses`, `cooldown_left` up to `cooldown_rounds`.
- Campaign models: `UniqueConstraint` on Session (`campaign`, `number`), Page (`campaign`, `slug`), BoardLink (`card_a`, `card_b`) and one active Encounter per campaign; `CheckConstraint` on BoardLink (`card_a` < `card_b`) and Combatant (a character or a name).
- `Campaign.status`: use `TextChoices` with `activa`, `pausada`, `terminada`.
- `ArmorDamage.type`: use `TextChoices` with `penetrating` (Penetrante) and `shredding` (Triturante). "None" and "both" are not choices: they are zero, or more than one, ArmorDamages linked to the attack.
- `Attack.stat_bonus`: choices `str` (Fuerza) and `dex` (Destreza), from the `Stat` TextChoices; empty means no ability modifier.
- `Weapon.to_hit`: a `RegexValidator` checks the formula (rule 14), with the same ability codes as `Stat`.

### `on_delete`

| Foreign key | `on_delete` | Effect |
|---|---|---|
| Link tables (ItemEffect, Inventory, ActiveEffect, CharacterAbility, RaceAbility, SubraceAbility, ClassAbility, SubclassAbility) | `CASCADE` | Rows disappear with either side |
| Campaign models → Campaign, and their parts (objectives, links, posts, locations, moves, combatants) → their whole | `CASCADE` | Deleting a campaign deletes its diary, wiki, board, scenes, maps and fights |
| Optional references inside a campaign (event → session or location, page → parent or location, quest → giver, note → page or author, post → author or character) | `SET_NULL` | The row stays without the reference |
| Subrace.race, Subclass.char_class | `CASCADE` | Deleting a race or class deletes its subraces or subclasses; blocked if a character uses one of them |
| Weapon.item, Armor.item | `CASCADE` | Deleting the Item deletes its weapon or armor data |
| Character.race, Character.subrace, Character.char_class, Character.subclass, Armor.armor_type | `PROTECT` | Cannot delete a race, subrace, class, subclass or armor type still in use |
| Character.player | `SET_NULL` | The character stays without a user |
| Character.template | `CASCADE` | Deleting an NPC template deletes its copies |
| Item.template | `PROTECT` | Cannot delete an item template that still has copies |

Plain many-to-many links, such as Character ↔ Campaign, have no `on_delete`: deleting either side only removes the link. That is how characters survive the deletion of their campaigns.
