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
| Characters | Character, Race, CharacterClass |
| Abilities | Ability, RaceAbility, ClassAbility |
| Spells and combat | Spell, Component, Attack, DiceRoll, ArmorDamage |
| Effects | Effect, ActiveEffect |
| Items and equipment | Item, Inventory, Weapon, Armor, ArmorType |

RaceAbility, ClassAbility, Inventory and ActiveEffect are **link tables** (yellow in the diagram): they join two models and carry extra data of their own.

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

- Has many Masters and many players (both are Users, see above).
- Contains (*contiene*) many Characters. A Character can belong to many Campaigns, or to none. Deleting a campaign only removes the link: its characters are kept, and those left without any campaign are shown apart.

---

## Characters

### Character

| Field | Type | Notes |
|---|---|---|
| name | string | |
| xp | int | |
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
| race_id | FK → Race | Required |
| is_npc | bool | True for NPCs (see NPCs below) |
| template_id | FK → Character | **Optional.** Only for NPC copies: the NPC template it was made from |

- Belongs to many Campaigns (*contiene*, many-to-many), or to none. There is no `campaign_id` column.
- Has (*posee*) many Abilities. An Ability can be held by many Characters, unless it is unique: then only one character per campaign (see game rules).
- Knows (*conoce*) many Spells.
- Carries (*lleva*) Items through Inventory.
- Suffers (*sufre*) Effects through ActiveEffect.

Not stored, calculated when needed:

- **speed**: from the character's Race.
- **maximum AP and maximum magic AP**: from the equipped armor (see game rules).
- **initiative**: from dexterity.
- **passive perception**: from wisdom.
- **level**: from xp. The conversion is not defined yet and is not needed for now.

### NPCs: templates and copies

NPCs are Characters with `is_npc = true`. There can be many of the same NPC (four goblins) and an NPC can be reused in many campaigns, so NPCs work with templates and copies (*es copia de*, Character → Character):

- **Template:** an NPC with no `template`, for example "Goblin". It is defined once and can be used in any campaign. A one-off NPC, such as a named shopkeeper, is simply a template that is never copied.
- **Copy:** an NPC whose `template` points to the template it was made from: "Goblin 1", "Goblin 2"… Each copy is an independent character with its own HP, AP, inventory, effects and campaigns.
- **Making a copy** duplicates the template's stats, abilities, spells, inventory and active effects. Item copies (individual weapons and armor) are copied again from the same item template, so each NPC copy has its own; item templates carried directly (gold, potions…) are shared. Campaigns are not copied: the copy is added to the campaigns it is made for.
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

### CharacterClass

| Field | Type | Notes |
|---|---|---|
| name | string | |
| primary_ability | string | |
| description | text | |

- Defines (*define*) many Characters.
- Grants (*otorga*) Abilities through ClassAbility.
- Can cast (*puede lanzar*) many Spells. A Spell can be available to many classes.

---

## Abilities

### Ability

| Field | Type | Notes |
|---|---|---|
| name | string | |
| unique | bool | If true, only one character per campaign can have it. Not exclusive for characters without a campaign (see game rules). |
| description | text | |

- Applies (*aplica*) many Effects.

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

Race and class abilities are kept in two tables on purpose: both foreign keys are real and never empty. A single table would need either an always-empty column or a foreign key the database cannot check.

---

## Spells and combat

### Spell

| Field | Type | Notes |
|---|---|---|
| name | string | |
| level | int | 0 = cantrip (*truco*) |
| casting_time | string | |
| range | int | In meters |
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
| range | int | In meters |
| area | int | In meters |
| cooldown | int | In rounds |

- Performed by Weapons and by Spells (*realiza*, many-to-many in both cases).
- Rolls (*tira*) many DiceRolls. One attack can combine several dice rolls of different kinds.
- Inflicts (*inflige*) ArmorDamages (many-to-many). Most attacks have none; see ArmorDamage for the four cases.
- Applies (*aplica*) many Effects.

### DiceRoll

| Field | Type | Notes |
|---|---|---|
| amount | int | Number of dice |
| sides | int | Sides per die. Amount 2 and sides 6 means 2d6. |

### ArmorDamage

| Field | Type | Notes |
|---|---|---|
| type | string | `penetrating` or `shredding` |
| amount | int | Number of AP points affected |

An attack is in one of four armor-damage cases. Only two of them are values of `type`; the other two come from how many ArmorDamages are linked to the attack:

- **None:** the attack has no ArmorDamage linked. Not a value of `type`. This is why armor damage is its own table: attacks without it carry no empty columns.
- **Penetrating** (`penetrating`): the attack ignores `amount` AP points.
- **Shredding** (`shredding`): after dealing damage, the attack subtracts `amount` AP points.
- **Both:** the attack has more than one ArmorDamage linked, a penetrating one and a shredding one. Not a value of `type` either. Shredding is always applied first (see game rules).

---

## Effects

### Effect

| Field | Type | Notes |
|---|---|---|
| name | string | |
| description | text | |
| duration | string | |

Applied (*aplica*, many-to-many) by Spells, Abilities, Attacks, Items and ArmorTypes. Weapon and armor effects are attached through their Item.

### ActiveEffect (link table)

| Field | Type | Notes |
|---|---|---|
| character_id | FK → Character | |
| effect_id | FK → Effect | |
| remaining_turns | int | |

The effects currently on a character.

---

## Items and equipment

### Item

| Field | Type | Notes |
|---|---|---|
| name | string | |
| price | int | |
| description | text | |
| consumable | bool | |
| template_id | FK → Item | **Optional.** Only for copies: the item template it was made from |

- Gold is an Item; how much a character has is the quantity in Inventory.
- Deleting a character does not delete its items, except for NPC copies (see NPCs).
- A Weapon or an Armor *is an* (*es un*) Item: one-to-one.
- Applies (*aplica*) many Effects.

### Item templates and copies

Items work with templates and copies (*es copia de*, Item → Item), like NPCs. The template field lives only on Item: a Weapon or an Armor follows the Item it belongs to, so one field covers all three.

- **Template:** an item with no `template`, for example "Espada larga" or "Oro". It is a catalog entry, defined once. A template's Weapon or Armor row describes a kind of weapon or armor.
- **Copy:** an item whose `template` points to the template it was made from. It is one physical piece with its own Weapon or Armor row, so equipping or upgrading it changes only that piece.
- **Making a copy** duplicates the item's data, effects and Weapon or Armor row, including its attacks. A copy made from another copy points to the same template.
- **Weapons and armor are carried as copies:** characters never carry a weapon or armor template. Generic items (gold, potions…) are templates carried directly; how many a character has is the quantity in Inventory.
- A template cannot be deleted while it has copies: delete the copies first.

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
| to_hit | string | A number or a formula, converted to int when attacking |
| equipped | bool | Only copies can be equipped |

A template's Weapon row describes a **kind** of weapon; a copy's Weapon row is an **individual** weapon (see Item templates and copies).

- Performs (*realiza*) many Attacks.

### Armor

| Field | Type | Notes |
|---|---|---|
| item_id | FK → Item | One-to-one |
| armor_type_id | FK → ArmorType | Required |
| ap | int | This piece's own AP; raised when the armor is upgraded |
| magic_ap | int | This piece's own magic AP |
| equipped | bool | Only copies can be equipped |

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

## Game rules the code must enforce

These are house rules or constraints the diagram cannot express.

1. **Hit points:** `hp` is the maximum and `temp_hp` is the current value.
2. **Armor points:** AP and AC are separate stats. Magic AP protects against magic damage.
   - **Maximum AP is not stored on the character.** It is calculated for the character sheet from the equipped armor: `Armor.ap + ArmorType.ap`, and `Armor.magic_ap + ArmorType.magic_ap` for magic AP. If no armor is equipped, both maximums are 0.
   - The two parts are stored separately: `Armor.ap` is that piece's own AP, already including its upgrades, and `ArmorType.ap` is the amount every armor of that type adds.
   - Only current AP is stored on the character, as `temp_ap`, because it changes during combat. Magic AP has no stored current value: only its calculated maximum exists.
3. **Armor damage:** penetrating attacks ignore `amount` AP; shredding attacks subtract `amount` AP after dealing damage. An attack with both applies each of them, always shredding first and penetrating second.
4. **Attack damage:** the sum of all the attack's dice rolls plus `extra`.
5. **Equipment:**
   - A character can have several weapons equipped but only **one armor** equipped.
   - A character can only equip weapons and armor that are in their Inventory.
   - Armor and Weapon have no character column, because ownership goes through Inventory → Item. So these rules must be checked in code (model `clean()` or a service layer), not by a database constraint.
6. **Unique abilities:** an Ability with `unique = true` can belong to only one character per campaign. Characters in different campaigns can each have it.
   - A character in several campaigns must be the only one with that ability in each of them.
   - A character **cannot join** a campaign where another character already has one of its unique abilities.
   - A character cannot get a unique ability that another character already has in any of its campaigns.
   - Characters without a campaign keep their unique abilities, which stay marked as unique, but are not exclusive in any sense: they block no one and nothing blocks them, until they join a campaign.
   - Campaign membership and abilities are many-to-many links, so this is checked in code, not by a database constraint.
7. **Ability levels:** a character gets a race or class ability only once their level is at least `level_required`.
8. **One class per character:** no multiclassing.
9. **Calculated, not stored:** speed comes from the race; maximum AP and magic AP come from the equipped armor; initiative and passive perception come from the ability scores; level comes from xp (conversion to be defined later).
10. **Units:** distances (race speed, spell and attack range, attack area) are in meters; attack cooldown is in rounds. Distances in feet (D&D books, character sheets) are converted with 6 ft = 2 m, rounded to the nearest meter: 30 ft = 10 m, 35 ft = 12 m, 25 ft = 8 m.
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
| *define* | `Character.char_class = ForeignKey(CharacterClass)` |
| *posee* | `Character.abilities = ManyToManyField(Ability)` |
| *conoce* | `Character.spells = ManyToManyField(Spell)` |
| *lleva* | `Character.items = ManyToManyField(Item, through="Inventory")` |
| *sufre* | `Character.effects = ManyToManyField(Effect, through="ActiveEffect")` |
| *otorga* (race) | `Race.abilities = ManyToManyField(Ability, through="RaceAbility")` |
| *otorga* (class) | `CharacterClass.abilities = ManyToManyField(Ability, through="ClassAbility")` |
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
| *aplica* | `effects = ManyToManyField(Effect)` on Spell, Ability, Attack, Item and ArmorType |

The two User ↔ Campaign relationships need different `related_name` values, otherwise Django reports a clash. No other relationship needs one; `Character.campaigns` uses `related_name="characters"`, and `Character.template` and `Item.template` use `related_name="copies"`, only so they read as `campaign.characters` and `template.copies`.

### Constraints and choices

- `UniqueConstraint` on Inventory (`character`, `item`), RaceAbility (`race`, `ability`) and ClassAbility (`char_class`, `ability`).
- `CheckConstraint` on Character: `template` is empty or `is_npc` is true.
- `Campaign.status`: use `TextChoices` with `activa`, `pausada`, `terminada`.
- `ArmorDamage.type`: use `TextChoices` with `penetrating`, `shredding`. "None" and "both" are not choices: they are zero, or more than one, ArmorDamages linked to the attack.

### `on_delete`

| Foreign key | `on_delete` | Effect |
|---|---|---|
| Link tables (Inventory, ActiveEffect, RaceAbility, ClassAbility) | `CASCADE` | Rows disappear with either side |
| Weapon.item, Armor.item | `CASCADE` | Deleting the Item deletes its weapon or armor data |
| Character.race, Character.char_class, Armor.armor_type | `PROTECT` | Cannot delete a race, class or armor type still in use |
| Character.player | `SET_NULL` | The character stays without a user |
| Character.template | `CASCADE` | Deleting an NPC template deletes its copies |
| Item.template | `PROTECT` | Cannot delete an item template that still has copies |

Plain many-to-many links, such as Character ↔ Campaign, have no `on_delete`: deleting either side only removes the link. That is how characters survive the deletion of their campaigns.
