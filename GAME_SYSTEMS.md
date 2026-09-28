# Igris RPG & Life Simulation Game Engine Specification
Author: Rex Lapis (آریا) & Sofy (سوفی)
Status: Approved & Active Game Architecture

## 1. Core Concept & Atmosphere
- Platform: Native Telegram Group & DM RPG Bot (No WebApps/MiniApps).
- Theme: Urban Fantasy & Life Simulation (inspired by Solo Leveling / Meowie / Streetwear / Dark Fantasy).
- Group Dynamic: Every Telegram group acts as a living "City" (شهر) with its own economy, clans, and social drama.

---

## 2. Character & Appearance
- **Onboarding & Creation:** Post-creation shows the character image card cleanly with full status text.
- **Card UI:** No inline buttons under the final profile/identity card.
- **Commands:**
  - `کمد` (Wardrobe): Lists owned outfits, skins, and equipped cosmetics.
  - `آرایشگاه` (Barber/Salon): Changes hair style and hair color (costs gold/credits).
  - `زیبایی` (Aesthetics Clinic): Changes eyes, eyebrows, facial features (costs gold/credits).
  - `کلینیک` (Hospital/Clinic): Cures illnesses (random diseases or pregnancy sickness) for a medical fee.

---

## 3. Education, Career & Economy (RPG Life Engine)
- **Education (`تحصیل`):**
  - Players pay tuition to increase their Education Level (`سواد` / IQ).
  - Higher education unlocks higher-paying professions and increases success in complex activities.
- **Jobs & Career (`شغل`):**
  - Displays available professions based on the player's current Education Level.
  - Low education = low income jobs (courier, construction worker, etc.).
  - High education = elite high-income careers (engineer, doctor, CEO, etc.).
- **Work (`کار`):**
  - Triggers work activity and deposits salary into wallet based on active job and level. Has cooldown.
- **Wallet (`کیف پول`):**
  - Shows cash on hand, bank savings, debt, and net worth.
- **Shop (`فروشگاه`):**
  - Interactive inline button shop categorized into: Shirts/Tops, Pants/Bottoms, Accessories, Auras, Weapons, etc.
- **Banking (`بانک`):**
  - Deposit / savings with interest.
  - Loan system (`وام`): Borrow money based on existing wealth/credit score, repayable over time with interest.

---

## 4. Crime, Justice & Duels (Underworld Engine)
- **Heist / Theft (`دزدی`):**
  - Triggered by replying `دزدی` to a target player in a group chat.
  - Success Calculation: Thief Level vs Target Level + Education/IQ factor + Random luck roll.
  - Loot: Steals 35% to 85% of the target's cash on hand.
  - Failure & Jail:
    - Sent to Prison (`زندان`) for a cooldown period where all actions/commands are locked.
    - OR option to pay bail / fine equal to 5x the attempted theft amount.
- **Duels (`دوئل`):**
  - Challenge another player with a stake/bet.
  - Winner decided by Level, Education/Strategy stat, and Combat Luck roll.
  - Loser transfers the agreed stake to winner.

---

## 5. Social, Romance, Drama & Family (Social Engine)
- **Marriage (`ازدواج`):**
  - Unlocked at a specific Level threshold. Players propose and marry in the group.
- **Intimacy / Sex (`سکس` / `رابطه`):**
  - Available only between married couples.
  - Cooldown: e.g. once every 8 hours.
  - Pregnancy Chance: Low random chance of pregnancy per encounter.
- **Childbirth & Child Raising (`فرزند`):**
  - When pregnant, the mother becomes ill (`مریض`) and must visit the clinic for treatment and costs.
  - Baby is born after gestation period.
  - Children have ongoing upkeep costs: food, growth, and education tuition.
- **Affairs / Cheating (`خیانت`):**
  - Active only for married players who pursue secret relations.
  - Exposure Formula: If `Average(Cheaters' Level + Cheaters' Education)` > `Target Spouse's (Level + Education)` -> affair remains secret (plus luck roll). Otherwise exposed publicly in group!
- **Divorce (`طلاق`):**
  - Mutual agreement allows instant separation.
  - Contested Divorce: If one spouse refuses, the bot randomly selects an active group member as the **Judge (قاضی دادگاه)** to review the case and rule on the divorce!

---

## 6. City Ecosystem & Clan Wars (Group Dynamics)
- **Group as a City:** Groups collect taxes from shop purchases, clinic fees, and theft fines into a City Treasury.
- **Clans & Gangs (`کلن` / `گنگ`):**
  - Players can form clans within the group/city (e.g. Clan Rex).
  - Clan vaults, turf wars, alliances, and territory control battles.
