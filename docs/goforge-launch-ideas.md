# GoForge launch ideas and contributor kit

Draft ideas for the first GoForge rounds, plus what to send to the people who will submit them.

Ground rules (the same ones the site enforces and states publicly):

- Every idea is submitted by a **real person from their own wallet and their own X account**. Nobody submits under someone else's handle.
- The ideas below are **starting material**. A contributor may change anything, and the idea becomes theirs when they submit it. Say so openly if asked where the ideas came from.
- One idea per wallet per day. The team's own wallets and X accounts cannot submit or vote.
- All 24 ideas already pass the site's automatic checks (field rules, blocked names, stock tickers, near-duplicate check among themselves) and are tuned against the Meta Radar (see below). The image still has to be made: square, 512x512 or larger, PNG/JPG/WebP, at most 2 MB, no logos, no text-heavy art.

## What the Meta Radar says about reaching 30K

Read from the public Meta Radar on 2026-10-08 (run radar_2026-10-08, 2,245 tokens, 17 narratives). "Survival" is the share of resolved tokens that reached $30K.

| Group | Resolved | Reached 30K | Note |
|-------|----------|-------------|------|
| Chain baseline | 2,118 | 45.3% | CI 43.2-47.4% |
| **No narrative (unique, original lore)** | **842** | **68.3%** | CI 65.1-71.3%, normal confidence. The best group by far |
| Best narrative cluster ("holder / table / imaginary") | 43 | 48.8% | low confidence, barely above baseline |
| "Note / original / sock" cluster | 57 | 43.9% | normal confidence, at baseline |
| "Robinhood / chain / agents" cluster | 75 | 36.0% | below baseline |
| "Website" cluster | 176 | 14.8% | worst: tokens with a placeholder lore |
| Generic "DexScreener / market cap" cluster | 410 | 31.5% | auto-generated descriptions |

What this means for the ideas:

- Almost every Radar narrative is a **repeated one-line template** (for example "Every holder gets a seat at the table. The table is imaginary."). Copying a popular line puts an idea in a cluster that performs at or below the 45% baseline, and GoForge's Golem score then rates it from that cluster's lift.
- **Original, concrete, non-templated lore sits outside every cluster and reached 30K far more often (68%).** These 24 ideas are written for that group: a specific image, a character, and a small ritual, with no crypto buzzwords.
- Words that pull lore into weak clusters (holders, chart, roadmap, airdrop, liquidity, contract, chain, agents, website, "story of patience") are avoided on purpose.
- Lore length is a strong signal in the model. The Golem lore-quality score is full marks from 250 to 600 characters, so every lore here is 296-380 characters.

Checked against the real scoring code (gf_scoring.py) with the Radar's own embedder (MiniLM, ONNX) and the cluster examples published by the Radar: all 24 ideas fall outside every cluster (cosine distance 0.63 or more, the cut-off is 0.60), so narrative = 50 (neutral), lore quality = 100 and **Golem score = 67 for each**. One earlier draft ("Dust Library") landed inside the weak "patience / story" cluster (Golem score 54) and was rewritten as "Hollow Oak Library".

Limits of this check:

- The clusters were rebuilt from the five public example lores per cluster, not from the Radar's full centroids, so distances are approximate.
- Reaching 30K depends on much more than lore (liquidity, timing, community, who launches it). The Radar shows a strong association, not a guarantee. The 68% group may also differ from the rest in ways the Radar does not show.
- GoForge currently scores "no narrative" as neutral (50). The Radar suggests that group deserves more than neutral, which is a possible scoring change for the team to consider, not something these ideas rely on.

## Logo style (from the Pons launchpad)

Observed on ponsfamily.com/launchpad (New and Market cap tabs, 2026-10-08). The Axiom Discover page for Robinhood Chain could not be read: it sits behind a Cloudflare bot check, so nothing from it is used here.

What the logos on Pons look like:

- The list shows each logo at about **36-72 px** in a rounded square. Only a **single bold shape** survives that size; fine detail and text turn to mud.
- The biggest tokens by market cap (NovaAI, Pons, Delta, Longbow, Pare) use a **dark tile with one simple glyph or mark**. Cartoon and meme faces (Bundle Cat, Thinking Cat, RobinDog) work too, but only when the face is big and high contrast.
- Logos are stored as 128-768 px squares, so 512x512 is a safe size. Pure white or empty logos stand out as blank gray squares in the list (some tokens show exactly that).
- Colours: a dark background with **one saturated accent colour** reads best next to the white list background.

The GoForge logo style, built from that:

1. 512x512 PNG, dark vertical gradient tile (about #1A1410 to #0E0B09, tinted by the idea's colour).
2. **One flat glyph**, centred, filling roughly 60-70% of the tile, drawn with 2-3 flat colours (accent, a warm off-white, a dark detail colour).
3. A soft radial glow of the accent colour behind the glyph, and a soft drop shadow under it.
4. No text, no real-brand marks, nothing the logo check could read as a well-known logo.

The 24 logos in this style are in [docs/goforge-logos/](goforge-logos/), one PNG per ticker (`SPORE.png`, `LFROG.png`, ...), plus `contact-sheet.png` showing them large and at list size. `generate_logos.py` rebuilds them (`python generate_logos.py`), so a colour or shape can be changed in one place. All 24 pass the same image validator the backend uses (square, 512 px, under 2 MB).

## Ideas

| # | Name | Ticker | Lore | Image direction |
|---|------|--------|------|-----------------|
| 1 | Spore Keeper | SPORE | A tiny mushroom who remembers every holder by name and refuses to forget a single one of them, even when the market forgets the mushroom itself. Each night it counts its spores and whispers the names back into the soil. In the cold months the whole forest floor glows faintly, and travellers say it is only the mushroom keeping its promise to remember. | A small glowing mushroom with a lantern cap, tiny face, standing on a mossy stone at night, warm amber light, soft painted style. |
| 2 | Lantern Frog | LFROG | The lantern frog sings the tide back every evening so that the fishing boats can find the harbour lights again after the long grey storms of autumn. Nobody has ever seen it sleep, and the harbour has never been dark since. When the lantern flickers the frogs of the whole coast answer in a low chorus, and the boats turn toward shore without a word. | A green frog holding a paper lantern on a wet pier at dusk, harbour boats behind, teal and gold palette, storybook illustration. |
| 3 | Clock Tower | TOWER | Nobody remembers who built the clock tower, but every hour it rings one coin more than the hour before, and the town has stopped asking why. At midnight the bells ring twelve and the whole square holds its breath. Children in the square leave a coin on the lowest step each evening, and by morning it is always gone, and the bells are always a little warmer. | A tall stone clock tower in a quiet town square at night, golden clock face, small coins falling like rain, purple sky. |
| 4 | Paper Crane | CRANE | A paper crane folded from the first receipt ever printed, still flying, still carrying the same small promise across every border it meets. It lands on one window each dawn and leaves a single fold of luck behind. Those who find the folded luck keep it in a pocket, and nobody has ever managed to unfold it twice without it flying away again. | A red origami crane in flight over mountains at sunrise, subtle receipt text on its wings, clean flat colours. |
| 5 | Moss Oracle | MOSSY | A boulder so old that moss has learned to speak on its behalf. Travellers ask it about the road ahead and the moss answers slowly, one patient sentence per season, and it has never once been wrong about the weather. The old folk say the best question is the shortest one, because the moss only has the patience to answer questions that fit in a breath. | A giant mossy boulder with a calm carved face, ferns and tiny flowers, soft green light, painterly forest. |
| 6 | Ember Fox | EMBFOX | The ember fox walks through burnt forests and wakes the seeds that fire could not kill. Wherever it sleeps, a ring of new green grows by morning, and the villagers leave it warm bread at the edge of the ash. Foresters now plant their first sapling where the fox slept, and swear that the ground there stays warm through the whole of winter. | An orange fox curled up in a ring of fresh green sprouts inside a charred forest, glowing embers, cosy dramatic lighting. |
| 7 | Salt Lighthouse | SALTHO | A lighthouse built from salt blocks that melts a little every year and is rebuilt by the villagers every spring, brick by brick, because a light that nobody maintains is only a tall candle waiting for the rain. On the first night of spring the whole village climbs the shore with lanterns, and the light is switched on by the youngest child present. | A white salt-block lighthouse on a rocky shore, villagers carrying blocks up a path, warm sunset, detailed flat illustration. |
| 8 | Hollow Oak Library | OAKLIB | A tiny library inside a hollow oak, with shelves of pressed leaves that grow a new line every autumn, so no one has ever finished reading one. The librarian is a sleepy owl who stamps each return with a golden feather, and the whole room smells like rain on warm stone. Visitors leave with damp boots and quiet pockets. | The inside of a hollow oak tree shaped like a cosy library, owl on a ladder, glowing leaves on shelves, warm golden light, storybook illustration. |
| 9 | Tide Turtle | TIDEY | A turtle the size of an island who carries a small village on its shell and swims the same slow circle every year, so the villagers always know the season by which coast they can see from their windows. Visitors who stay for a season learn to sleep to the rhythm of its swimming, and leave unable to sleep anywhere that stands still. | A huge sea turtle with a tiny village and trees on its shell, calm blue ocean, sunrise haze, wide cinematic shot. |
| 10 | Night Market Cat | NMCAT | A black cat who runs the stall at the night market that only appears when it rains. It sells small jars of lucky silence, and the price is always whatever you can honestly afford to give away. Regulars bring jars of their own and swap them at the end of the night, and the cat keeps a quiet ledger of every fair trade. | A black cat behind a glowing market stall with paper lanterns in the rain, reflections on wet stones, neon warm tones. |
| 11 | Anvil Bee | ANVBEE | A bee that forges honey instead of making it, hammering each drop on a tiny anvil until it holds its shape for a hundred years. Beekeepers say its honey never spoils and never forgets the flowers it came from. Its hive hums in a single low note, and old beekeepers can tell the health of the whole valley just by listening for that one sound. | A golden bee with a tiny hammer beside a miniature anvil inside a honeycomb workshop, sparks, warm amber macro style. |
| 12 | Echo Canyon | ECHOCN | A canyon that answers every shout with the thing you needed to hear instead of the thing you said. Hikers leave puzzled, then quiet, and return years later with a friend and a thermos of tea. The canyon is quietest at noon, and the guides advise first-time visitors to arrive late, when the walls have warmed enough to speak. | A wide red canyon at golden hour with a lone hiker and visible soft sound waves in the air, serene, painted landscape. |
| 13 | Kite Monastery | KITEMN | A mountain monastery where monks send kites into the wind carrying one honest word each, and never pull them back. The sky above the valley is full of words nobody will ever read, and that is the whole point. Pilgrims who climb to the monastery are handed a kite and told to choose one honest word, and most spend the whole night deciding. | A mountain monastery with many colourful kites rising from the roof, misty peaks, calm pastel palette. |
| 14 | Glass Whale | GLSWHL | A whale made of glass that sings so softly the sea can see through its song. Sailors say it swims only where the water is clear and honest, and storms part around it like curtains. Fishermen who hear it sing stop their engines and drift, and the long glass shadow passes beneath them like a slow thought. | A translucent glass whale gliding through deep blue water with soft light refracting through its body, serene, ethereal. |
| 15 | Stone Gardener | STNGRD | A silent stone figure that tends a garden on a cliff edge, watering moss with a cracked clay jug. It has been working for ten thousand years, and the garden has only just begun to bloom. Hikers who sit with it for an hour say the garden seems to lean toward them, as if finally getting a visitor. | A weathered stone gardener with a clay jug beside a cliffside garden of tiny flowers, soft morning mist, quiet and warm. |
| 16 | Rainmaker Drum | RAINDR | A drum that was never struck by hand. It plays itself when the dry season ends, one slow beat at a time, and the farmers count the beats to know how many days remain before the first rain. On the last night of the dry season every dog in the village goes quiet and every farmer stands in a doorway, counting along. | An ancient wooden drum on a dry hill with distant clouds gathering and a few raindrops, earthy brown and blue palette. |
| 17 | Orbit Snail | ORBSNL | A snail that has been crawling around the rim of the moon for longer than anyone can count, leaving a silver trail the tides follow. It is in no hurry, and that is why the oceans have never lost their way. Astronomers log its crawl in their notebooks with a small drawing of a shell, and none of them has ever admitted why. | A glowing snail on the edge of a crescent moon with a faint silver trail across a starry sky, dreamy night palette. |
| 18 | Bridge Troll Club | TROLLB | A friendly troll who guards a bridge and charges every traveller one good story as the toll. The best stories are carved into the planks, and the bridge has become the most read road in the whole valley. Travellers who cannot think of a story are allowed to pay with a good silence, which the troll folds into his pocket for later. | A big gentle troll sitting under a stone bridge with carved planks and a crowd of tiny travellers, whimsical storybook style. |
| 19 | Wind Cartographer | WINDMAP | A mapmaker who draws only the paths that the wind takes, never the roads that people built. Her maps are useless to walkers and priceless to sailors, kite makers and anyone who wants to know where tomorrow is going. Her studio is filled with ribbons tied to wooden pegs, and every ribbon points somewhere that no road has ever reached. | A hand-drawn map with swirling wind lines pinned on a wooden desk with brass tools, warm parchment tones, top-down. |
| 20 | Lichen Knight | LICHEN | A knight so still for so long that lichen has covered his armour in soft orange and green. He guards a path nobody has used in a century, and he is proud that nobody needed to, because the guarding was the point. Children in the valley bring him acorns and tell him secrets, and he has never once moved, which they take as a promise kept. | A kneeling armoured knight covered in colourful lichen in a quiet forest clearing, soft dappled light, painterly. |
| 21 | Bell Fisher | BELLFS | A fisherman who catches sunken bells instead of fish and rings each one once before returning it to the sea. Divers say the ocean floor has begun to hum in answer, and the sound travels further every year. He keeps a small notebook of every bell he has rung, each page with a drawing and the date, and he will show it to anyone who asks. | A lone fisherman in a small boat lifting a green bronze bell from dark water at dawn, mist, muted blue and bronze. |
| 22 | Pocket Comet | POCOMT | A tiny comet that lives in a coat pocket and warms cold hands on winter nights. It was never meant to leave the sky, but it fell in love with the idea of being useful and decided to stay. The comet hums when it is held and goes dark when it is lonely, so its owner keeps it close and always talks to it on the way home. | A small glowing comet resting inside a woollen coat pocket with a hand reaching toward it, snow falling, warm light. |
| 23 | Harbor Lamp Lizard | HLIZRD | A lizard that sleeps on the harbour lamp so that the light always stays warm to the touch. Fishermen leave it a crumb of bread each morning, and in return the lamp has never once gone out in a storm. Sailors say the harbour never feels like home until they see its small green shape curled around the glass, glowing faintly in the lamp. | A small green lizard asleep on top of an old harbour street lamp at night, boats and water below, cosy teal and amber. |
| 24 | Thread Mother | THRDMM | An old spinner who keeps every promise ever made in the village as a single thread on her loom. When a promise is broken the thread frays, and she mends it quietly before anyone has to feel ashamed. The village children are taught that a promise is a thread, and she will teach any of them to mend one if they come before sundown. | An elderly spinner at a large wooden loom with glowing threads of many colours, warm candlelit room, detailed and gentle. |

## How to invite contributors

Pick people who genuinely want to take part (community members, friends of the project, creators who follow Epoch Labs). Ask first, and let them choose which idea to take or write their own. Do not give anyone an idea to submit without their agreement, and do not ask for their wallet keys or X password: they sign in themselves.

### Message to send (English)

> Hey! GoForge by Epoch Labs is opening. Anyone can submit a token idea, EPC holders vote, and every day Golem scores the pool, picks one winner and launches it on Pons. The creator earns half of the token's trading fees.
> We'd love for you to submit one idea in the first rounds. We drafted a few starter ideas you can take, change or ignore, or bring your own. You sign in with your own wallet and X account, so the idea is yours.
> Submissions are open 00:00-12:00 UTC. Link: <site>/goforge

### Message to send (Bahasa Indonesia)

> Hai! GoForge dari Epoch Labs sudah dibuka. Siapa saja bisa mengirim ide token, pemegang EPC memilih, dan tiap hari Golem menilai semua ide, memilih satu pemenang, lalu meluncurkannya di Pons. Kreator mendapat setengah dari fee trading token itu.
> Kami ingin kamu mengirim satu ide di ronde-ronde awal. Kami siapkan beberapa ide awal yang boleh kamu pakai, ubah, atau abaikan, atau bawa ide sendiri. Kamu login dengan wallet dan akun X milikmu sendiri, jadi idenya atas namamu.
> Submit dibuka 00:00-12:00 UTC. Link: <situs>/goforge

### Steps for a contributor

1. Open the site, **Connect wallet**, and sign the message (no gas).
2. **Connect X** and approve the login. Only the handle and public profile numbers are read.
3. Fill in the name, ticker and lore. Make or generate a square image from the image direction.
4. **Submit idea.** Review takes place before it enters the pool.
5. Voting runs 12:00-20:00 UTC. Voters need at least 1,000 EPC in a wallet older than 7 days.

## Suggested spread

The pool is public once submissions close, so spread ideas across contributors and days instead of one person sending many. With one idea per wallet per day, 24 ideas need either 24 contributors in one round or about 6 contributors over 4 rounds.

| Round | Ideas | Contributors needed |
|-------|-------|---------------------|
| Day 1 | 1-8 | 8 |
| Day 2 | 9-16 | 8 |
| Day 3 | 17-24 | 8 |
