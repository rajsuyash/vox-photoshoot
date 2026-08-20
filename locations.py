"""Shoot location presets, and the prompt they compose into.

The whole point of the 3-step UX is that the client never writes a prompt. They pick
a face and a place; everything a photographer would decide — lens, light, framing,
wardrobe — is baked into the preset here.

Every preset must keep the jewellery readable. Locations are therefore written as
BACKGROUND, deliberately thrown out of focus, never as a wide establishing shot: a
model standing in front of the Taj Mahal at full length shows no earring at all.
"""

import re
from dataclasses import dataclass

import product

# Nouns that only ever describe something worn. Used to prove a location plate stays a
# photograph of an empty place: a plate that mentions a saree has become a shoot.
GARMENT_WORDS = re.compile(
    r'\b(saree|sari|blouse|kurta|kaftan|lehenga|dupatta|shawl|pashmina|gown|dress|'
    r'coat|shirt|trousers|sleeves|polo neck|chignon|cardigan|scarf|veil)\b',
    re.IGNORECASE)


@dataclass(frozen=True)
class Location:
    key: str
    label: str
    region: str
    scene: str      # what is behind the model, deliberately blurred, for shoots
    light: str      # time of day and quality of light
    wardrobe: str   # what she wears, chosen to not fight the jewellery
    plate: str      # the empty place itself, sharp and unpopulated, for the picker card


INDIAN = [
    Location(
        key='amber-fort',
        label='Amber Fort, Jaipur',
        region='India',
        scene='carved amber sandstone arches and jali latticework of a Rajasthani palace '
              'courtyard, softly blurred behind her',
        light='warm golden hour sunlight raking across the stone, soft bounce onto her face',
        wardrobe='deep red and gold silk banarasi saree',
        plate='the carved amber sandstone courtyard of Amber Fort in Jaipur, scalloped arches and jali lattice screens, warm stone underfoot, empty of people',
    ),
    Location(
        key='udaipur-palace',
        label='City Palace, Udaipur',
        region='India',
        scene='white marble columns and scalloped arches of a lakeside palace terrace, '
              'Lake Pichola shimmering out of focus beyond',
        light='cool bright morning light with soft reflected glow from the water',
        wardrobe='pastel mint and silver chanderi saree',
        plate='a white marble lakeside terrace of the City Palace in Udaipur, scalloped arches and carved columns, Lake Pichola and the far hills beyond, empty of people',
    ),
    Location(
        key='kerala-backwaters',
        label='Kerala Backwaters',
        region='India',
        scene='coconut palms leaning over still green backwater, a wooden houseboat '
              'far out of focus',
        light='humid diffused afternoon light under overcast sky, gentle and even',
        wardrobe='cream and gold kasavu saree',
        plate='a still green Kerala backwater channel lined with leaning coconut palms, a wooden houseboat moored along the bank, empty of people',
    ),
    Location(
        key='taj-mahal',
        label='Taj Mahal, Agra',
        region='India',
        scene='white marble domes and minarets rising softly blurred in the far background',
        light='pale pink dawn light, low sun, cool shadows',
        wardrobe='ivory and pale blue georgette saree',
        plate='the white marble Taj Mahal in Agra seen across its reflecting pool and formal gardens, minarets on either side, empty of people',
    ),
    Location(
        key='rann-of-kutch',
        label='Rann of Kutch',
        region='India',
        scene='endless white salt flat meeting a wide empty horizon, no landmarks',
        light='dusk light after sunset, soft magenta and blue sky, very even',
        # Mirror-work and heavy embroidery render as jewellery and out-compete the
        # earring — every wardrobe here stays plain above the shoulders.
        wardrobe='plain black cotton kutchi outfit with no embroidery or mirror work',
        plate='the white salt flats of the Rann of Kutch, cracked hexagonal salt crust stretching to a flat empty horizon, no structures and no people',
    ),
]

FOREIGN = [
    Location(
        key='paris',
        label='Paris, France',
        region='International',
        scene='Haussmann stone facades and wrought iron balconies of a Paris boulevard, '
              'heavily blurred, warm bokeh from a cafe behind',
        light='soft overcast European daylight, cool and flattering',
        wardrobe='tailored ivory wool coat over a simple black top',
        plate='a Paris boulevard of Haussmann stone facades and wrought iron balconies, a corner cafe with awning and rattan chairs, empty pavement',
    ),
    Location(
        key='santorini',
        label='Santorini, Greece',
        region='International',
        scene='whitewashed cycladic walls and a blue dome, deep blue Aegean far below, '
              'thrown out of focus',
        light='bright Mediterranean late afternoon sun, strong warm key with white wall bounce',
        wardrobe='flowing white linen dress with long sleeves covering her shoulders',
        plate='a whitewashed Santorini terrace in Oia, blue domed church and cubic white houses stepping down the caldera, deep blue Aegean below, empty of people',
    ),
    Location(
        key='dubai-desert',
        label='Dubai Desert',
        region='International',
        scene='rolling golden sand dunes with soft wind-carved ridges, no structures',
        light='low amber sunset light, long soft shadows, warm rim light on her jaw',
        wardrobe='bronze silk kaftan',
        plate='rolling golden sand dunes in the desert outside Dubai, sharp wind carved ridges and long shadows, no structures and no people',
    ),
    Location(
        key='lake-como',
        label='Lake Como, Italy',
        region='International',
        scene='a stone villa terrace with cypress trees and the lake and mountains '
              'softly blurred behind',
        light='clear late morning Italian light, bright with soft shade on her face',
        wardrobe='navy silk dress with elbow length sleeves covering her shoulders',
        plate='a stone villa terrace on Lake Como with a balustrade, tall cypress trees, the lake and mountains beyond, empty of people',
    ),
    Location(
        key='kyoto',
        label='Kyoto, Japan',
        region='International',
        scene='tall green bamboo grove, vertical stalks receding into soft blur',
        light='cool filtered green-tinted daylight through the canopy, very diffused',
        wardrobe='minimal charcoal grey wrap top',
        plate='a tall green bamboo grove in Arashiyama Kyoto, dense vertical stalks lining a narrow path, empty of people',
    ),
]


# Indoor and studio locations. The original ten are all outdoors, which is a real gap for
# jewellery: a bridal set photographs best in controlled interior light, and every
# marketplace listing wants a clean studio backdrop rather than a palace courtyard.
#
# The same rule as everywhere else in this file applies — the scene is BACKGROUND, written
# to be thrown out of focus. A location that competes with the piece has failed, however
# beautiful it is.
INDOOR = [
    Location(
        key='marble-studio',
        label='White Marble Studio',
        region='Studio',
        scene='a seamless soft white studio backdrop with a faint warm gradient, nothing '
              'else in shot',
        light='large softbox key from the front left with a white bounce fill, clean and '
              'even, no coloured cast',
        wardrobe='simple ivory silk blouse with a high neckline',
        plate='a clean empty photographic studio with a seamless white marble sweep, a '
              'large softbox to one side, polished floor, nothing else in the room',
    ),
    Location(
        key='charcoal-studio',
        label='Charcoal Studio',
        region='Studio',
        scene='a deep charcoal grey seamless studio backdrop falling off to near black at '
              'the edges',
        light='single dramatic softbox from high left, deep soft shadows, strong falloff',
        wardrobe='matte black high necked long sleeved top',
        plate='an empty photographic studio with a deep charcoal seamless backdrop, one '
              'large softbox on a stand, dark polished floor, nothing else in the room',
    ),
    Location(
        key='haveli-interior',
        label='Haveli Interior, Jaipur',
        region='India',
        scene='carved sandstone pillars and frescoed walls of an old Rajasthani haveli '
              'room, softly blurred behind her',
        light='warm shafts of afternoon sun through a jali screen, dust in the air, deep '
              'warm shadow',
        wardrobe='deep emerald silk kurta with full sleeves',
        plate='the interior room of an old Jaipur haveli, carved sandstone pillars, '
              'frescoed walls, a jali screen throwing patterned light across a stone '
              'floor, empty of people',
    ),
    Location(
        key='chettinad-mansion',
        label='Chettinad Mansion',
        region='India',
        scene='polished athangudi tiled floor and teak columns of a Chettinad courtyard '
              'house, deep shade beyond',
        light='cool even daylight from an open central courtyard, soft and diffused',
        wardrobe='mustard and maroon kanjivaram silk saree',
        plate='the pillared inner courtyard of a Chettinad mansion in Tamil Nadu, teak '
              'columns, patterned athangudi tile floor, open sky above the courtyard, '
              'empty of people',
    ),
    Location(
        key='mirror-palace',
        label='Sheesh Mahal',
        region='India',
        scene='thousands of tiny convex mirrors set into an arched palace ceiling and '
              'walls, catching light as soft bokeh',
        light='warm candlelight and low lamps multiplied across the mirrorwork, glittering '
              'and intimate',
        wardrobe='deep red velvet lehenga blouse with full sleeves',
        plate='the mirrored hall of a Sheesh Mahal palace chamber, arched ceiling inlaid '
              'with thousands of small mirrors, low warm lamps, empty of people',
    ),
    Location(
        key='mumbai-artdeco',
        label='Art Deco Apartment, Mumbai',
        region='India',
        scene='curved art deco interior with terrazzo floor, rounded teak cabinetry and '
              'a tall shuttered window behind',
        light='bright tropical daylight through wooden louvres, warm slatted shadow',
        wardrobe='crisp white cotton shirt dress',
        plate='a 1930s art deco apartment interior in Bombay, terrazzo floor, curved teak '
              'cabinetry, tall louvred shutters half open to bright light, empty of people',
    ),
    Location(
        key='library',
        label='Old Library',
        region='International',
        scene='floor to ceiling dark wood bookshelves and a brass reading lamp, deeply '
              'blurred behind her',
        light='warm pooled lamplight against cool window light, low and moody',
        wardrobe='camel cashmere polo neck',
        plate='the reading room of an old private library, floor to ceiling dark wood '
              'shelves packed with books, a long table with brass lamps, empty of people',
    ),
    Location(
        key='atelier',
        label='Jeweller\'s Atelier',
        region='International',
        scene='a workbench of fine tools, loupes and a felt tray, warm wood and brass, '
              'thrown well out of focus',
        light='focused warm task lamp with cool daylight from a high window behind',
        wardrobe='oatmeal linen shirt with sleeves rolled to the elbow',
        plate='a jeweller\'s workshop bench with hand tools in a rack, a loupe, a felt '
              'tray and a brass lamp, warm wood surfaces, nobody at the bench',
    ),
    Location(
        key='hotel-suite',
        label='Grand Hotel Suite',
        region='International',
        scene='a panelled hotel suite with heavy silk drapes and a gilt mirror softly out '
              'of focus behind',
        light='warm lamplight with cool blue evening light from tall windows',
        wardrobe='midnight blue silk gown with long sleeves',
        plate='a grand hotel suite interior, panelled walls, heavy silk curtains at tall '
              'windows, a gilt framed mirror and a chaise, empty of people',
    ),
    Location(
        key='greenhouse',
        label='Victorian Greenhouse',
        region='International',
        scene='wrought iron glasshouse ribs and dense green foliage pressing against '
              'misted glass',
        light='soft white light diffused through fogged glass, gentle and shadowless',
        wardrobe='sage green linen dress with long sleeves',
        plate='the inside of a Victorian glasshouse, white painted iron ribs and misted '
              'panes, palms and ferns crowding the path, empty of people',
    ),
]

# Outdoor additions. Weighted toward India, because that is where the customers are, with
# enough international range for a brand shooting a diaspora or luxury campaign.
MORE_OUTDOOR = [
    Location(key='varanasi-ghats', label='Varanasi Ghats', region='India',
             scene='worn stone ghat steps down to the Ganges with distant boats, hazy and '
                   'softly blurred',
             light='pale gold early morning haze, soft and low contrast',
             wardrobe='ochre and cream cotton saree',
             plate='the stone ghat steps of Varanasi descending to the Ganges, moored '
                   'wooden boats, temple spires in morning haze, empty of people'),
    Location(key='hampi', label='Hampi Ruins', region='India',
             scene='golden granite boulders and carved temple columns of Hampi, blurred '
                   'behind her',
             light='hot clear late afternoon sun with warm reflected bounce off stone',
             wardrobe='burnt orange cotton kurta with full sleeves',
             plate='the granite boulder landscape and carved stone temple colonnades of '
                   'Hampi in Karnataka, empty of people'),
    Location(key='munnar-tea', label='Munnar Tea Estate', region='India',
             scene='rolling emerald tea terraces receding into blue hill mist',
             light='cool misty highland morning light, very soft and even',
             wardrobe='cream wool shawl over a high necked blouse',
             plate='rolling tea plantation terraces in the Munnar hills of Kerala, neat '
                   'green rows curving over the slopes, blue mist beyond, empty of people'),
    Location(key='goa-beach', label='Goa Beach at Dusk', region='India',
             scene='wet sand reflecting a pink and gold sky, palms as dark shapes far '
                   'behind',
             light='low warm afterglow with soft pink fill from the wet sand',
             wardrobe='flowing white cotton dress with long sleeves',
             plate='an empty Goa beach at dusk, wet sand mirroring a pink and gold sky, '
                   'a line of palms along the shore, no people'),
    Location(key='jodhpur-blue', label='Blue City, Jodhpur', region='India',
             scene='indigo washed walls and narrow stepped lanes of old Jodhpur, softly '
                   'blurred',
             light='bright midday sun bounced blue off the painted walls, cool fill',
             wardrobe='pale gold and ivory bandhani dupatta over a plain blouse',
             plate='a narrow stepped lane in the blue painted old city of Jodhpur, indigo '
                   'washed walls and carved doorways, empty of people'),
    Location(key='himalayan-monastery', label='Himalayan Monastery', region='India',
             scene='ochre and crimson monastery walls with prayer flags, snow peaks far '
                   'beyond and out of focus',
             light='thin brilliant high altitude light, cold blue shadow and warm sun',
             wardrobe='deep maroon wool wrap with long sleeves',
             plate='a Ladakhi Buddhist monastery courtyard, whitewashed and ochre walls, '
                   'strings of prayer flags, snow capped peaks behind, empty of people'),
    Location(key='mysore-palace', label='Mysore Palace Grounds', region='India',
             scene='domed indo-saracenic palace towers and manicured lawns, thrown out of '
                   'focus behind her',
             light='warm evening light just before the palace lamps come on',
             wardrobe='peacock blue mysore silk saree',
             plate='the grounds of Mysore Palace at evening, domed indo-saracenic towers '
                   'and formal lawns, empty of people'),
    Location(key='pondicherry', label='French Quarter, Pondicherry', region='India',
             scene='mustard and white colonial facades with bougainvillea over a shaded '
                   'street, blurred',
             light='warm dappled shade under flowering trees, soft and mottled',
             wardrobe='white and indigo block printed cotton dress',
             plate='a street in the French quarter of Pondicherry, mustard yellow colonial '
                   'walls, white shuttered windows, bougainvillea spilling over, no people'),
    Location(key='kashmir-shikara', label='Dal Lake, Kashmir', region='India',
             scene='still lake water with carved shikara boats and distant chinar trees, '
                   'soft and hazy',
             light='cool silver morning light off the water, gentle and diffused',
             wardrobe='cream pashmina shawl over a long sleeved kurta',
             plate='Dal Lake in Kashmir at dawn, carved wooden shikara boats moored on '
                   'still water, houseboats and chinar trees along the shore, no people'),
    Location(key='meghalaya-forest', label='Living Root Forest, Meghalaya', region='India',
             scene='dense dripping green rainforest with moss covered roots, very soft '
                   'and deep',
             light='green filtered forest light, damp and diffused, no direct sun',
             wardrobe='deep forest green cotton wrap with long sleeves',
             plate='a living root bridge in the rainforest of Meghalaya, moss covered '
                   'roots woven across a stream, dense wet green canopy, empty of people'),
    Location(key='amalfi', label='Amalfi Coast', region='International',
             scene='lemon terraces and pastel houses stacked above a blue sea, thrown out '
                   'of focus',
             light='bright Mediterranean morning sun with warm stone bounce',
             wardrobe='pale yellow linen dress with elbow sleeves',
             plate='the Amalfi coast in Italy, pastel houses stacked on cliffs above a '
                   'deep blue sea, lemon terraces, empty of people'),
    Location(key='marrakech-riad', label='Marrakech Riad', region='International',
             scene='zellige tiled courtyard walls and a still plunge pool, softly blurred',
             light='hot filtered light through a fretwork screen, warm with deep shade',
             wardrobe='rust coloured kaftan with long sleeves',
             plate='the tiled inner courtyard of a Marrakech riad, zellige mosaic walls, '
                   'a still rectangular pool, orange trees in pots, empty of people'),
    Location(key='kyoto-temple', label='Kyoto Temple Garden', region='International',
             scene='raked gravel, moss and a dark timber temple veranda, deeply blurred',
             light='overcast Japanese daylight, silver and completely even',
             wardrobe='charcoal grey wrap coat',
             plate='a Zen temple garden in Kyoto, raked white gravel, moss mounds and a '
                   'dark timber veranda, empty of people'),
    Location(key='iceland-black-sand', label='Black Sand Coast, Iceland', region='International',
             scene='black volcanic sand and basalt columns under a wide pale sky',
             light='flat cold northern light, silver and shadowless',
             wardrobe='heavy cream knit polo neck',
             plate='a black volcanic sand beach in Iceland with basalt columns and white '
                   'surf under a pale grey sky, no people'),
    Location(key='london-townhouse', label='London Townhouse Steps', region='International',
             scene='black railings and a glossy painted door of a Georgian terrace, '
                   'blurred behind',
             light='soft grey London daylight, cool and even',
             wardrobe='tailored charcoal wool coat',
             plate='the front steps of a Georgian London townhouse, black iron railings, '
                   'a glossy painted door with brass furniture, empty pavement'),
    Location(key='newyork-loft', label='New York Loft', region='International',
             scene='cast iron columns and huge industrial windows with the city beyond, '
                   'well out of focus',
             light='cool north light flooding through tall panes, soft and directional',
             wardrobe='black polo neck and tailored trousers',
             plate='a Manhattan loft interior with cast iron columns, exposed brick and '
                   'huge industrial windows onto the city, bare wooden floor, no people'),
    Location(key='swiss-alps', label='Swiss Alps in Winter', region='International',
             scene='snow laden pines and a white mountain slope, softly blurred',
             light='crisp blue-white alpine light with bright snow bounce',
             wardrobe='ivory wool coat with a high collar',
             plate='a snow covered alpine slope in Switzerland with snow laden pines and '
                   'jagged peaks under clear winter light, no people'),
    Location(key='seville-courtyard', label='Seville Courtyard', region='International',
             scene='whitewashed arches, ceramic tile and potted geraniums, thrown out of '
                   'focus',
             light='warm Andalusian afternoon light with strong white wall bounce',
             wardrobe='deep red silk dress with long sleeves',
             plate='a whitewashed Andalusian courtyard in Seville, horseshoe arches, '
                   'painted ceramic tile and pots of red geraniums, empty of people'),
    Location(key='dubai-skyline', label='Dubai Skyline at Night', region='International',
             scene='a wall of lit glass towers far below, reduced to soft golden bokeh',
             light='cool blue night with warm city light as rim and fill',
             wardrobe='structured black gown with long sleeves',
             plate='the Dubai skyline at night seen from high up, lit glass towers and '
                   'traffic light trails far below, no people'),
    Location(key='provence-lavender', label='Provence Lavender Field', region='International',
             scene='converging rows of purple lavender running to a distant stone farmhouse',
             light='warm low evening sun with a hazy purple cast',
             wardrobe='soft lilac linen dress with long sleeves',
             plate='rows of lavender in full flower in Provence converging toward a stone '
                   'farmhouse and cypress trees, empty of people'),
]


# The last ten, weighted to interiors and to the bridal and festive settings an Indian
# jeweller actually shoots for. Bridal is the highest value category in the business and
# the original ten had nowhere to shoot it.
MORE_INDOOR = [
    Location(key='bridal-room', label='Bridal Room', region='India',
             scene='a softly lit bridal dressing room with marigold garlands and a large '
                   'gilded mirror, blurred behind her',
             light='warm low lamplight with a soft gold bounce, intimate',
             wardrobe='deep red and gold bridal lehenga blouse with full sleeves',
             plate='an Indian bridal dressing room, a gilded mirror, marigold garlands, '
                   'silk cushions and a low carved stool, warm lamplight, empty of people'),
    Location(key='mandap', label='Wedding Mandap', region='India',
             scene='a flower decked wedding mandap with marigold and rose strings, deeply '
                   'out of focus behind her',
             light='warm evening light with strings of small lamps as golden bokeh',
             wardrobe='ivory and gold silk saree with a full sleeved blouse',
             plate='an Indian wedding mandap decorated with marigold and rose garlands, '
                   'carved pillars, brass lamps and low seating, empty of people'),
    Location(key='temple-corridor', label='Temple Corridor', region='India',
             scene='a long colonnade of carved granite temple pillars receding into warm '
                   'darkness',
             light='shafts of warm light between pillars with deep shadow between them',
             wardrobe='deep magenta silk saree with a full sleeved blouse',
             plate='the pillared stone corridor of a south Indian temple, carved granite '
                   'columns receding into shadow, oil lamps in niches, empty of people'),
    # The vitrines are EMPTY on purpose. A showroom stocked with other pieces puts a
    # competitor's necklace in soft focus behind the client's necklace, and the plate is
    # also the backplate — so it would follow the piece into the shoot.
    Location(key='boutique', label='Luxury Boutique', region='India',
             scene='softly lit empty glass vitrines and warm wood cabinetry of a luxury '
                   'showroom, well blurred',
             light='warm focused showroom lighting with clean cool fill',
             wardrobe='champagne silk blouse with long sleeves',
             plate='the interior of an empty luxury boutique, bare lit glass display '
                   'vitrines with nothing inside them, warm wood cabinetry, a velvet '
                   'seat at a counter, no people and nothing on display'),
    Location(key='courtyard-monsoon', label='Monsoon Courtyard', region='India',
             scene='rain falling into an open stone courtyard, wet dark stone and green '
                   'leaves, softly blurred',
             light='cool grey monsoon light with wet reflected sheen, very diffused',
             wardrobe='deep teal cotton saree with a full sleeved blouse',
             plate='an open stone courtyard of a Kerala house during monsoon rain, water '
                   'falling from tiled eaves onto wet dark stone, green plants, no people'),
    Location(key='terrace-sunset', label='City Terrace at Sunset', region='India',
             scene='a rooftop terrace with the hazy city skyline and water tanks far '
                   'behind, thrown out of focus',
             light='warm low sun with a dusty pink haze and long soft shadows',
             wardrobe='soft peach chikankari kurta with full sleeves',
             plate='an Indian rooftop terrace at sunset, low parapet wall, potted plants, '
                   'a hazy city skyline of rooftops and water tanks beyond, no people'),
    Location(key='paris-apartment', label='Paris Apartment', region='International',
             scene='herringbone parquet, a marble fireplace and tall casement windows, '
                   'deeply blurred',
             light='soft grey Parisian window light with warm interior fill',
             wardrobe='cream silk blouse with long sleeves',
             plate='a classic Paris apartment interior, herringbone parquet floor, marble '
                   'fireplace with a gilt mirror, tall casement windows onto a balcony, '
                   'empty of people'),
    Location(key='gallery-white', label='Art Gallery', region='International',
             scene='a bare white gallery wall with a single large canvas well out of '
                   'focus behind her',
             light='clean neutral gallery lighting, even and shadowless',
             wardrobe='structured white shirt dress',
             plate='an empty white walled art gallery with polished concrete floor, one '
                   'large abstract canvas on the far wall, track lighting, no people'),
    Location(key='opera-box', label='Opera House Box', region='International',
             scene='red velvet and gilded plasterwork of a theatre box, warm and deeply '
                   'blurred',
             light='low warm house lights before curtain, rich and shadowed',
             wardrobe='black velvet gown with long sleeves',
             plate='a gilded opera house box with red velvet seats and heavy drapes, the '
                   'auditorium and chandelier beyond, empty of people'),
    Location(key='kyoto-machiya', label='Kyoto Machiya Interior', region='International',
             scene='tatami, shoji paper screens and dark timber posts, very softly blurred',
             light='soft white light filtered through paper screens, gentle and even',
             wardrobe='dove grey linen wrap with long sleeves',
             plate='the interior of a traditional Kyoto machiya townhouse, tatami mats, '
                   'shoji paper screens, dark timber posts and a small garden beyond, '
                   'empty of people'),
]

ALL = {location.key: location for location in
       INDIAN + FOREIGN + INDOOR + MORE_OUTDOOR + MORE_INDOOR}


# Held constant across every shoot: the part that protects product fidelity, and not
# exposed in the UI. Everything here is true of any piece — what is specific to where
# the piece is worn (which body part stays bare, how it is framed, what must not be
# invented alongside it) lives on product.Category instead.
CRAFT_BASE = (
    'The jewellery must match the reference image exactly in shape, proportion, stone '
    'layout and metal colour, with no redesign. Shot on an 85mm lens at f/2, tack '
    'sharp focus on the jewellery, background thrown well out of focus. Photorealistic, '
    'natural skin texture with visible pores, no beauty retouching, luxury jewellery '
    'brand campaign photograph. She is dressed exactly as described above, fully and '
    'modestly, with her shoulders covered. '
    'Full bleed photograph filling the entire frame edge to edge, with no white border, '
    'mount or frame around it.'
)

# One shoot returns several genuinely different photographs, not one photograph several
# times: num_images alone produces near-duplicates, so framing is varied explicitly and
# each variant is given its own seed.
#
# The keys, not the prose, live here. Every category writes its own three framings —
# a ring cannot be shot on the crop that works for an earring — but the key names are
# fixed, because shoot.SEEDS, app.merge_images and the reshoot endpoint all address a
# frame by name.
FRAMINGS = ('hero', 'profile', 'detail')

# What compose() will accept. Deliberately NOT part of FRAMINGS: a shoot costs
# len(FRAMINGS) credits, so adding 'custom' there would silently reprice every shoot from
# three credits to four. 'custom' is a single client-composed shot, priced separately.
ALL_FRAMINGS = FRAMINGS + ('custom',)


def compose_plate(location_key: str) -> str:
    """Prompt for an EMPTY location plate — the picker card, and the backplate that a
    model gets composited onto later. No model, no product, no wardrobe, nothing blurred.
    """
    location = ALL[location_key]
    # The light strings are written for a shoot ("soft bounce onto her face"), so any
    # clause that talks about the model is dropped before reusing them on an empty plate.
    light = ', '.join(
        clause for clause in location.light.split(', ')
        if ' her' not in f' {clause}'
    )
    return (
        f'Photorealistic travel and architectural photograph of {location.plate}. '
        f'{light}. '
        'Wide establishing shot of the empty location, deserted and unoccupied, '
        'everything in sharp focus front to back, shot on a 24mm lens at f/8, '
        'high end location scouting photograph for a fashion campaign. '
        'Full bleed photograph filling the entire frame edge to edge, with no white '
        'border, mount or frame around it.'
    )


def compose(product: str, category, model_description: str, location_key: str,
            framing: str = 'hero', options=None, comp=None) -> str:
    """Build the prompt from the three things the client picked, plus what they uploaded.

    category is a product.Category — it decides where on the body the piece goes, how
    the frame is cropped, and what must not be invented next to it. options is a
    product.Options carrying what the photograph cannot say: how big the piece is, and
    which finger it goes on.
    """
    import composition as composition_module

    location = ALL[location_key]
    if framing not in ALL_FRAMINGS:
        raise KeyError(f'unknown framing {framing!r}; have {sorted(ALL_FRAMINGS)}')
    comp = comp or composition_module.Composition()
    # Free text from the client, placed after the craft rules so it can override them —
    # "keep the engraving" has to beat a generic instruction about sharpness.
    note = (options.instructions or '').strip() if options else ''

    # In CUSTOM mode the framing line comes only from the client's frame and distance.
    # Mixing it with category.framings would put two answers to the same question in one
    # prompt — "extreme close up of the hand" and "her whole figure" — and the model
    # resolves that contradiction arbitrarily.
    if framing == 'custom':
        frame_line = comp.custom_framing(category.key)
    else:
        frame_line = f'{category.framings[framing]} {comp.direction(category.key)}'.strip()

    return (
        # Expression sits near the front deliberately: at the end of the prompt it was
        # ignored and every shot came back neutral. It used to be a hardcoded smile,
        # which meant a brand wanting a composed, serious campaign could not have one at
        # any price — the default is still a smile, but it is now the client's to change.
        f'{model_description}, '
        f'{composition_module.EXPRESSIONS[comp.expression]}, '
        # "from the reference image" stays here at the front, next to the product, even
        # though CRAFT_BASE restates fidelity later: the early anchor is what stopped
        # the model redesigning the piece, and CRAFT_BASE alone did not.
        f'wearing {product} from the reference image {category.worn(options)} '
        f'She wears a {location.wardrobe}. '
        f'Behind her: {location.scene}. '
        f'Lighting: {location.light}. '
        f'Framing: {frame_line} '
        f'{CRAFT_BASE} {category.craft} '
        + (f'{note} ' if note else '')
        + category.negative
    )


def _check_composition(category) -> None:
    """The composition controls must actually reach the prompt.

    Every one of these is a silent failure otherwise: the client picks 'serious', the
    prompt still says 'smiling', and the only way anyone finds out is by looking at a
    photograph they paid for.
    """
    import composition

    base = compose(product='p', category=category, model_description='m',
                   location_key='kyoto', framing='hero')

    # The default must be exactly what the hardcoded line used to be, or every existing
    # shoot silently changes character the day this ships.
    assert 'smiling warmly with a genuine open smile' in base

    serious = compose(product='p', category=category, model_description='m',
                      location_key='kyoto', framing='hero',
                      comp=composition.parse({'expression': 'serious'}, category.key))
    assert 'not smiling' in serious and 'genuine open smile' not in serious

    # View, angle and pose each have to change the prompt on their own.
    for field, value, needle in (('view', 'side', 'full profile'),
                                 ('angle', 'top-down', 'directly overhead'),
                                 ('pose', 'tucking-hair', 'tucks her hair')):
        got = compose(product='p', category=category, model_description='m',
                      location_key='kyoto', framing='hero',
                      comp=composition.parse({field: value}, category.key))
        assert needle in got, f'{field}={value} never reached the prompt'
        assert got != base

    # Custom mode must build its framing from the client's frame and distance, and must
    # NOT carry the shot-owned crop as well — two answers to one question.
    custom = compose(product='p', category=category, model_description='m',
                     location_key='kyoto', framing='custom',
                     comp=composition.parse({'frame': 'ear', 'distance': 'close'},
                                            category.key))
    assert 'one ear fills the frame' in custom and 'Shot close' in custom
    for fixed in FRAMINGS:
        assert category.framings[fixed] not in custom, f'custom leaked the {fixed} crop'

    # An unknown framing is still a loud failure, not an unframed prompt.
    try:
        compose(product='p', category=category, model_description='m',
                location_key='kyoto', framing='nonsense')
    except KeyError:
        pass
    else:
        raise AssertionError('an unknown framing produced a prompt')


def demo() -> None:
    # Structure, not a count: an exact number goes stale every time a location is added
    # and teaches whoever hits it to edit the number rather than read the check.
    assert len(ALL) >= 10, len(ALL)
    assert len({l.key for l in ALL.values()}) == len(ALL), 'duplicate location key'
    # An Indian jeweller sells mostly at home, so India must never be the thin end.
    indian = [l for l in ALL.values() if l.region == 'India']
    assert len(indian) >= len(ALL) / 3, f'only {len(indian)} Indian locations of {len(ALL)}'
    # Every location must carry all four strings, or it renders a prompt with a hole in
    # it that reads as a missing sentence and is invisible until someone sees the image.
    for location in ALL.values():
        for field in ('scene', 'light', 'wardrobe', 'plate'):
            assert getattr(location, field).strip(), f'{location.key}.{field} is empty'
        # The plate is the empty place. A model in it means the picker card shows a
        # person the client did not choose.
        assert any(phrase in location.plate for phrase in
                   ('empty', 'no people', 'nobody', 'deserted', 'unoccupied')), \
            f'{location.key} plate never says the place is unpeopled'

    earrings = product.CATEGORIES['earrings']
    prompt = compose(
        product=product.DEFAULT_PRODUCT,
        category=earrings,
        model_description='An Indian woman in her mid twenties with fair wheatish skin '
                          'and long straight dark hair',
        location_key='amber-fort',
    )
    assert 'Amber' not in prompt, 'label leaks into prompt; scene text should stand alone'
    assert 'reference image' in prompt

    # Each framing must actually change the prompt, or a "shoot" is one photo repeated.
    rendered = {
        name: compose(product='p', category=earrings, model_description='m',
                      location_key='kyoto', framing=name)
        for name in FRAMINGS
    }
    assert len({*rendered.values()}) == len(FRAMINGS), 'framings collapsed to one prompt'

    _check_composition(earrings)

    # And the category must reach the prompt: this is the bug that put a ring on an ear.
    # Only the half before CRAFT_BASE is checked — the craft and negative clauses name
    # the ear on purpose, to say it stays bare and empty.
    ring = compose(product='a gold solitaire ring', category=product.CATEGORIES['ring'],
                   model_description='m', location_key='kyoto', framing='detail')
    aimed = ring.split(CRAFT_BASE)[0]
    assert 'ring finger' in aimed, aimed
    assert 'earring' not in aimed, 'ring prompt still poses the piece as an earring'

    # The client's own choices have to survive into the prompt, or the controls are
    # decoration. Scale especially: a product shot carries no size reference, so this
    # sentence is the only thing telling the model how big the piece is.
    chosen = compose(
        product='a gold signet ring', category=product.CATEGORIES['ring'],
        model_description='m', location_key='kyoto', framing='hero',
        options=product.Options(size='xl', type='signet', finger='index', hand='left',
                                instructions='Keep the engraving crisp.'),
    )
    assert 'index finger of her left hand' in chosen, chosen
    assert 'signet' in chosen and 'twice the width of her fingernail' in chosen
    assert 'Keep the engraving crisp.' in chosen
    # The note must land before the negative hint, which stays last.
    assert chosen.index('Keep the engraving') < chosen.index('Do not invent')

    # Every size must produce a different prompt for every category.
    for key, category in product.CATEGORIES.items():
        rendered = {compose(product='p', category=category, model_description='m',
                            location_key='kyoto', options=product.Options(size=size))
                    for size in product.SIZES}
        assert len(rendered) == len(product.SIZES), f'{key} ignores the size control'

    try:
        compose(product='p', category=earrings, model_description='m',
                location_key='kyoto', framing='wide')
    except KeyError:
        pass
    else:
        raise AssertionError('unknown framing should be rejected')

    # Plates must describe a place and nothing else: no model, no product, no wardrobe.
    for key, location in ALL.items():
        card = compose_plate(key)
        # \bher\b, not ' her'. A substring test fires on 'herringbone parquet' — the
        # same trap that made 'earrings' match 'ring' and 'near' match 'ear' elsewhere
        # in this project.
        assert not re.search(r'\bher\b', card), f'{key} plate mentions the model: {card}'
        assert 'earring' not in card and 'jewellery' not in card, key
        # Garment NOUNS, not the wardrobe's first word. That was the old test and it
        # fires on 'deep emerald silk kurta' because the lighting also says 'deep warm
        # shadow' — a colour adjective shared between clothing and light is not a leak.
        # What actually matters is a plate describing something worn.
        assert not re.search(GARMENT_WORDS, card), f'{key} plate leaks wardrobe: {card}'
        assert 'out of focus' not in card, f'{key} plate should be sharp throughout'
    print('plates ok')

    print(prompt)


if __name__ == '__main__':
    demo()
