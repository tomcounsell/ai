"""
Concrete, tangible word lists for maximum creative distance.

Simple, everyday words that force metaphorical thinking.
Includes animal behaviors (individual and collective) and natural phenomena.
"""

VERBS = {
    "wild": [
        # Human actions
        "painting", "baking", "melting", "climbing", "swimming",
        "knitting", "planting", "folding", "pouring", "carving",
        "juggling", "mixing", "stacking", "rowing", "hammering",
        "sweeping", "digging", "sewing", "polishing", "watering",
        # Animal actions (individual)
        "flying", "burrowing", "hunting", "nesting", "molting",
        "shedding", "prowling", "grazing", "soaring", "diving",
        "stalking", "hibernating", "camouflaging", "gliding", "perching",
        # Animal actions (collective)
        "swarming", "flocking", "herding", "schooling", "migrating",
        "clustering", "scattering", "regrouping", "signaling", "coordinating",
        # Defense/protection
        "shielding", "hiding", "sheltering", "guarding", "protecting",
        "retreating", "evading", "dodging", "defending", "blocking",
        # Mechanical/systematic actions
        "organizing", "filing", "sorting", "labeling", "measuring",
        "calculating", "scheduling", "documenting", "recording", "indexing",
        "aligning", "calibrating", "standardizing", "categorizing", "numbering",
        # Controlled production
        "manufacturing", "assembling", "packaging", "processing", "refining",
        "sterilizing", "sealing", "preserving", "refrigerating", "storing",
        # Regulated movement
        "queueing", "marching", "synchronizing", "timing", "pacing",
        "rationing", "distributing", "allocating", "designating", "assigning",
        # Maintenance
        "inspecting", "monitoring", "testing", "verifying", "auditing",
        "updating", "backing", "copying", "duplicating", "archiving",
        # Healing/restoration
        "healing", "mending", "repairing", "restoring", "smoothing",
        "cleaning", "washing", "drying", "brushing", "combing",
        "bandaging", "stitching", "patching", "fixing", "curing",
        # Gentle care
        "cradling", "rocking", "stroking", "patting", "hugging",
        "cushioning", "padding", "wrapping", "tucking", "covering",
        "feeding", "nursing", "nourishing", "hydrating", "warming",
        # Growth/construction
        "growing", "blooming", "sprouting", "flourishing", "thriving",
        "building", "reinforcing", "strengthening", "supporting", "stabilizing",
        # Calming
        "soothing", "calming", "quieting", "settling", "resting",
        "sleeping", "dreaming", "meditating", "breathing", "relaxing",
        # Digital/cyber actions
        "uploading", "downloading", "hacking", "encrypting", "streaming",
        "syncing", "interfacing", "booting", "compiling", "rendering",
        "scanning", "digitizing", "projecting", "transmitting",
        # Mechanical/robotic
        "powering", "charging", "rebooting", "overclocking", "modulating",
        "amplifying", "augmenting", "droning",
        # Space/advanced tech
        "teleporting", "warping", "cloaking", "phasing", "beaming",
        "pressurizing", "terraforming", "mining", "docking", "launching",
        # Energy manipulation
        "ionizing", "magnetizing", "polarizing", "channeling",
        "focusing", "dispersing", "absorbing", "converting"
    ],
    "chaos": [
        # Destructive human actions
        "crushing", "burning", "drowning", "exploding", "rotting",
        "bleeding", "screaming", "choking", "rusting", "cracking",
        "tearing", "dragging", "stomping", "freezing", "shattering",
        # Animal actions (survival/predation - OFFENSE)
        "devouring", "clawing", "biting", "stinging", "mauling",
        "ambushing", "pouncing", "strangling", "trampling", "pecking",
        "stampeding", "attacking", "overwhelming", "infesting", "consuming",
        # Animal actions (survival/defense - DEFENSE)
        "escaping", "fortifying", "armoring", "burrowing", "camouflaging",
        "fleeing", "adapting", "regenerating", "healing", "surviving",
        # Biological/organic sci-fi
        "mutating", "evolving", "spawning", "metamorphosing", "replicating",
        "assimilating", "infecting", "colonizing", "spreading", "morphing",
        "splitting", "merging", "budding", "gestating",
        # Psychic/mental
        "telepathizing", "mind-melding", "probing", "sensing", "communing",
        "dream-walking", "imprinting",
        # Otherworldly movement
        "dimension-hopping", "time-sliding", "probability-shifting", "quantum-tunneling",
        "gravitating", "levitating", "undulating", "pulsating", "resonating",
        # Exotic biology
        "secreting", "crystallizing", "bioluminescing", "photosynthesizing",
        "liquefying", "solidifying", "sporing", "cocooning"
    ]
}

NOUNS = {
    "wild": [
        # Everyday objects
        "shoe", "door", "window", "chair", "spoon",
        "rope", "mirror", "ladder", "bucket", "pencil",
        "cup", "key", "clock", "fork", "basket",
        "candle", "pillow", "blanket", "bottle", "bowl",
        # Animal structures and features
        "nest", "hive", "web", "shell", "wing",
        "anthill", "cocoon", "tunnel", "den", "warren",
        "feather", "scale", "fin", "antenna", "horn",
        # Natural elements
        "rain", "river", "tree", "seed", "leaf",
        "rock", "cave", "hill", "path", "root",
        # Protection structures
        "wall", "fence", "shelter", "roof", "gate",
        "hedge", "burrow", "den", "hideout", "safe",
        # Office/institutional items
        "clipboard", "folder", "binder", "stapler", "calculator",
        "label", "barcode", "badge", "uniform", "schedule",
        "cubicle", "filing cabinet", "printer", "scanner", "spreadsheet",
        # Manufactured/processed
        "plastic", "metal", "concrete", "glass", "wire",
        "pipe", "circuit", "battery", "screen", "keyboard",
        # Measurement tools
        "ruler", "scale", "timer", "thermometer", "gauge",
        "meter", "counter", "sensor", "monitor", "detector",
        # Containers/organization
        "compartment", "drawer", "slot", "rack", "grid",
        "container", "box", "tray", "bin", "shelf",
        # Comfort items
        "bed", "quilt", "cushion", "hammock", "cradle",
        "towel", "soap", "lotion", "balm", "tea",
        "book", "lamp", "rug", "curtain", "slippers",
        # Healing/medical
        "bandage", "medicine", "salve", "compress", "splint",
        "vitamin", "herb", "remedy", "treatment", "cure",
        # Safe spaces
        "home", "garden", "park", "library", "nursery",
        "haven", "meadow", "pasture", "grove", "glade",
        # Gentle elements
        "breeze", "dew", "mist", "moonlight", "sunrise",
        "stream", "pond", "shade", "grass", "petal",
        # Common devices
        "datapad", "neural-jack", "bio-scanner", "plasma-cutter", "nano-injector",
        "quantum-drive", "fusion-core", "gravity-plate", "stasis-pod", "cyberdeck",
        "memory-chip", "holo-projector", "force-field", "laser-grid",
        # Ship/station parts
        "airlock", "bulkhead", "thruster", "reactor", "conduit",
        "hull", "viewport", "cargo-bay", "med-bay", "cryo-chamber",
        # Weapons/tools
        "stunner", "disruptor", "multi-tool", "fabricator", "recycler",
        "beacon", "drone", "servo", "actuator", "sensor",
        # Infrastructure
        "terminal", "console", "relay", "node", "hub",
        "port", "socket", "cable", "circuit", "processor"
    ],
    "chaos": [
        # Violent natural phenomena (OFFENSE)
        "fire", "storm", "flood", "avalanche", "earthquake",
        "wildfire", "lightning", "thunder", "erosion", "mud",
        # Animal predation (OFFENSE)
        "venom", "prey", "predator", "trap", "jaws",
        "fang", "claw", "sting", "tooth", "swarm",
        "horde", "pack", "colony", "brood",
        # Wounds/damage (OFFENSE)
        "blood", "bone", "wound", "scar", "ash",
        "smoke", "shadow", "rust", "thorn", "ice",
        # Defense structures (DEFENSE)
        "fortress", "armor", "shield", "bunker", "moat",
        "rampart", "barrier", "sanctuary", "refuge", "vault",
        # Biological structures
        "spore", "pod", "sac", "membrane", "tendril",
        "chitin", "ichor", "pheromone", "enzyme", "symbiont",
        "hive-node", "neural-cluster", "bio-mass", "egg-sac", "chrysalis",
        # Alien materials
        "xenocrystal", "bio-gel", "star-dust", "void-stone", "plasma-silk",
        "quantum-foam", "dark-matter", "exotic-particle", "time-crystal", "null-space",
        # Alien anatomy
        "tentacle", "mandible", "proboscis", "gill-slit", "compound-eye",
        "exoskeleton", "bio-luminescent-organ", "psionic-lobe", "venom-gland", "spinneret",
        # Alien environments
        "spawning-pool", "growth-chamber", "mind-web", "fungal-network", "coral-structure",
        "bio-dome", "flesh-wall", "living-ship"
    ]
}