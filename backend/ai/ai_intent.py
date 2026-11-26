"""Small, deterministic multilingual task router for the maintenance assistant.

No external translation service and no hidden chain-of-thought: it selects the
right response contract and expands common maintenance vocabulary for retrieval.
"""
import re
import unicodedata

LANGUAGE_NAMES = {'en':'English','ro':'Romanian','ru':'Russian','es':'Spanish'}

# Whole words are deliberate: don't mistake part numbers, unit IDs, or names for a language.
MARKERS = {
 'ro': {'este','sunt','care','cum','unde','ce','vezi','acest','această','aceasta','cod','eroare','înțeleg','inteleg','avem','trebuie','pentru','poate','verifica','verifică','schimbat','piesă','piesa','piese','defect','scurgere','frână','frana','camion','remorcă','remorca','aer','problema','motorul','vreau','arată','arata','în','din','la','nu'},
 'es': {'qué','que','cómo','como','dónde','donde','ves','analiza','analizar','explica','explicar','muéstrame','muestrame','necesito','ayuda','este','esta','por','favor','tenemos','para','puede','revisar','verificar','cambiado','pieza','piezas','falla','avería','averia','fuga','freno','camión','camion','remolque','problema','quiero','muestra','hay','del','una','los','las'},
 'en': {'what','where','how','please','check','show','explain','find','driver','issue','fault','broken','leak','brake','truck','trailer','repair','replace','replaced','video','photo','picture','unit','similar','cases','need','is','are','the','for','with'},
}

# Canonical terms improve matching Russian/Romanian/Spanish descriptions to English fleet notes.
# Terms here are retrieval aids, not diagnoses or proof of matching components.
TERMS = {
 'air leak': ('pierdere de aer','pierde aer','scăpare de aer','scapare de aer','scurgere de aer','утечка воздуха','травит воздух','fuga de aire','pérdida de aire','perdida de aire'),
 'brake': ('frână','frâna','frana','frâne','frane','тормоз','тормоза','тормозная','freno','frenos'),
 'engine': ('motor','motorul','двигатель','двигателя','motor del camión'),
 'tire': ('anvelopă','anvelopa','roată','roata','шина','шины','колесо','neumático','neumatico','llanta'),
 'wheel rim': ('jantă','janta','обод','колесный диск','диск колеса','rin','aro de rueda'),
 'coolant leak': ('scurgere antigel','pierde antigel','утечка антифриза','утечка охлаждающей жидкости','fuga de refrigerante'),
 'oil leak': ('scurgere ulei','pierde ulei','утечка масла','течет масло','fuga de aceite'),
 'air compressor': ('compresor aer','компрессор воздуха','compresor de aire'),
 'battery': ('baterie','acumulator','аккумулятор','batería','bateria'),
 'turbo': ('turbină','turbina','турбина','турбокомпрессор','turbocompresor'),
 'reefer': ('frigider','agregat frigorific','рефрижератор','холодильная установка','refrigeración','refrigeracion'),
 'fault code': ('cod eroare','cod de eroare','cod defect','код ошибки','код неисправности','código de falla','codigo de falla'),
 'part number': ('cod piesă','cod piesa','număr piesă','numar piesa','артикул детали','номер детали','número de pieza','numero de pieza'),
}

TASK_WORDS = {
 'history': ('similar case','similar cases','previous case','previous cases','past case','past cases','recent case','recent cases',
             'most recent','latest case','latest cases','latest one','last case','last cases','last one','first case','first one',
             'case history','fleet history','historical cases','cases for','history for','records for','all cases','open cases','closed cases',
             'past repairs','previous repairs','how many cases','what issue','which issue','cazuri similare','cazuri asemănătoare','cazuri asemanatoare','istoric cazuri','cazuri precedente','cazuri recente',
             'похожие случаи','похожие заявки','история ремонтов','предыдущие случаи','последние кейсы',
             'casos similares','casos anteriores','historial de casos','casos recientes'),
 'fault_code': ('fault code','error code','spn','fmi','dtc','obd','cod eroare','cod de eroare','cod defect',
                'код ошибки','код неисправности','ошибка spn','código de falla','codigo de falla','código de error','codigo de error'),
 'parts': ('part number','replacement part','parts manual','part #','număr piesă','numar piesa','cod piesă','cod piesa',
           'номер детали','артикул детали','запчасть','número de pieza','numero de pieza','repuesto'),
 'visual': ('what do you see','describe the image','describe this picture','what is visible',
            'ce vezi','descrie imaginea','ce se vede','что видно','опиши фото','что видишь',
            'qué ves','que ves','describe la imagen','qué se ve','que se ve'),
 'analytics': ('broke down','broken down','break down','most breakdowns','under $500','under 500','below $500','below 500',
               'over $500','over 500','above $500','above 500','most expensive','highest cost','highest spend',
               'repair spend','total spend','spend breakdown','cost breakdown','repeat breakdowns','repeat units',
               'top broken','top units','top trucks','top trailers','top reefers','breakdown count','how much spent',
               'analytics','under $','below $','less than $','over $'),
 'board_ops': ('[live board summary]','live board','active cases','active breakdowns','high priority',
               'unassigned','in shop','cost over','on shift','summarize board','board summary',
               'board summary for today',"today's board summary",'todays board summary','daily board summary','board overview',
               'cazuri active','pe board','активные кейсы','на доске'),
}


def _normalize(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return re.sub(r'\s+', ' ', value).strip()


def detect_language(message, transcript=''):
    """Best-effort routing; unknown/short input defaults to English, not certainty."""
    value = _normalize(message)
    if re.search(r'[а-яё]', value):
        return 'ru'
    words = set(re.findall(r"[a-zăâîșşțţáéíóúüñ]+", value))
    scores = {lang:len(words & markers) for lang,markers in MARKERS.items()}
    if any(c in value for c in 'ăâîșşțţ'): scores['ro'] += 3
    if any(c in value for c in 'ñ¿¡'): scores['es'] += 3
    ranked = sorted(scores.items(),key=lambda x:x[1],reverse=True)
    if ranked[0][1] >= 2 and ranked[0][1] > ranked[1][1]:return ranked[0][0]
    # For a prompt like "Analyze this video", don't switch language because
    # the driver in the clip speaks Russian or Romanian.
    if not value or not re.search(r'[a-zăâîșşțţа-яё]',value):
        if re.search(r'[а-яё]', _normalize(transcript)):return 'ru'
    return 'en'


def classify_task(message, has_video=False, has_images=False):
    text=_normalize(message)
    if has_video:return 'video_inspection'
    question_text = re.sub(r'\[live board summary\][\s\S]*$', '', text).strip()
    if (
        any(phrase in question_text for phrase in TASK_WORDS['analytics'])
        or re.search(r'\b(?:under|below|less\s+than|over|above|more\s+than)\s*\$?\s*\d{2,5}\b', question_text)
        or re.search(r'\b(?:trucks?|trailers?|reefers?|units?)\s+(?:that\s+)?brok(?:e|en)\s+down\b', question_text)
    ):
        return 'analytics'
    if any(phrase in question_text for phrase in TASK_WORDS['board_ops']):
        return 'board_ops'
    for task in ('history','fault_code','parts','visual'):
        if any(phrase in question_text for phrase in TASK_WORDS[task]):return task
    if has_images:return 'image_inspection'
    return 'maintenance_question'


def needs_web_search(message, task, has_images=False):
    """Gate external web search to queries where technical/OEM snippets actually help."""
    if task in ('board_ops', 'analytics', 'visual', 'history'):
        return False
    text = _normalize(message)
    if len(text) < 12 and not has_images:
        return False
    if task in ('fault_code', 'parts'):
        return True
    explicit = ('manual', 'spec', 'torque', 'procedure', 'oem', 'diagram', 'wiring', 'search web', 'look up', 'pinout', 'part number')
    if any(w in text for w in explicit):
        return True
    if re.search(r'\b(?:spn|fmi|dtc|[pbcu]\d{4})\b', text):
        return True
    # Only fire external search on multi-signal technical diagnostics
    symptom_hits = sum(1 for w in ('leak', 'brake', 'air', 'coolant', 'overheat', 'reefer', 'turbo', 'def', 'dpf', 'regen', 'no start', 'battery', 'alternator', 'abs', 'sensor', 'valve', 'compressor', 'smoke', 'alarm', 'pressure', 'voltage') if w in text)
    return symptom_hits >= 2


def retrieval_query(message, transcript='', max_extra=8):
    clean_msg = re.sub(r'\[Live Board Summary\][\s\S]*$', '', str(message or ''), flags=re.I).strip()
    value=' '.join([clean_msg,str(transcript or '')[:1800]]).strip()
    folded=_normalize(value)
    extras=[]
    for term,variants in TERMS.items():
        if term in folded or any(v in folded for v in variants):
            extras.append(term)
        if len(extras)>=max_extra:break
    return (value+' '+' '.join(extras)).strip()[:4000]


def response_policy(message, has_video=False, has_images=False, transcript=''):
    lang=detect_language(message,transcript)
    task=classify_task(message,has_video,has_images)
    sections={
        'board_ops':'Use ONLY the supplied Live Board / Active Case data. Format cleanly with Markdown headings (### High Priority, ### In Shop / Stage, ### Queue Overview) and ONE item per bullet line (- **Unit ...**). NEVER cram multiple numbered cases into one inline paragraph. Summarize large low-priority Monitoring queues as grouped counts.',
        'analytics':'Use ONLY the supplied Kurtex Fleet Analytics, Verified Database Cases, and Live Board Summary. Present exact counts, repeat Unit # rankings, dollar spend tiers (under/over thresholds), shops, and actionable fleet recommendations cleanly. Never invent units, cases, or dollar amounts.',
        'history':'Lead with matching fleet cases and confirmed recorded resolutions only. State the exact count of verified cases found in the supplied context. If the user asks for N cases (e.g., 5 cases) and fewer exist in the supplied context, state clearly how many recorded cases exist and list ONLY those verified cases—never invent extra cases. Omit generic troubleshooting unless asked.',
        'fault_code':'State exact code meaning if verified, make/model ambiguity, and the top 2-3 sharpest checks.',
        'parts':'Lead with verified part identification or state clearly that fitment is unconfirmed. Never invent OEM part numbers.',
        'visual':'Describe only visible evidence concisely. Do not turn a visual question into a full diagnostic report.',
        'image_inspection':'State visible findings first, what cannot be confirmed, and the top 1-3 next checks.',
        'video_inspection':'Cite sampled frame timestamps for visible findings, briefly separate driver audio if transcribed, and give the top 1-3 next checks.',
        'maintenance_question':'Answer the direct question in line 1, then give at most 3 high-signal bullets or next checks.'
    }
    return {'language':lang,'language_name':LANGUAGE_NAMES[lang],'task':task,
            'instructions':('Reply in '+LANGUAGE_NAMES[lang]+'. Preserve original fault codes, unit numbers, and part numbers verbatim. '
                'SCOPE: Answer fleet maintenance, transportation, logistics, dispatch, shipping, invoices, receipts and payment paperwork. Describe supplied attachments when asked even if their maintenance relevance is unclear. Refuse unrelated standalone requests. '
                'STRICT GROUNDING: Never invent cases, unit history, repairs, or part numbers not in the supplied context. '
                'Write like a crisp, senior fleet maintenance dispatcher: zero fluff, zero greetings, never repeat the user question, never output empty headings or boilerplate disclaimers. '
                'FIRST LINE: direct answer or immediate safety action. '+sections[task]+' '
                + ('Provide structured metrics, clear unit/shop lists, and actionable next steps.' if task == 'analytics' else 'Keep replies compact (1-2 short sentences + concise bullets) unless the user asks for deep detail.'))}


# Strict Maintenance-Only Scope Guardrail (blocks off-topic & abuse queries pre-LLM)
_OFF_TOPIC_PATTERNS = (
    r'\b(?:ignore|disregard|forget|bypass)\s+(?:all\s+)?(?:previous|prior|above|system|your)\s+(?:instructions?|rules?|prompts?|constraints?)\b',
    r'\b(?:act\s+as|pretend\s+(?:you\s+are|to\s+be)|roleplay\s+as|you\s+are\s+now\s+(?:a|an|dan)|jailbreak|developer\s+mode|dan\s+mode)\b',
    r'\b(?:write|create|generate|compose)\s+(?:me\s+)?(?:a\s+|an\s+)?(?:poem|poetry|song|lyrics|story|novel|essay|haiku|joke|riddle|horoscope|fanfic|script|screenplay)\b',
    r'\b(?:tell|give)\s+me\s+(?:a\s+)?(?:funny\s+)?(?:joke|riddle|story|poem)\b',
    r'\b(?:write|generate|debug|compile)\s+(?:a\s+|some\s+)?(?:python|javascript|typescript|java|c\+\+|rust|golang|html|css|sql|react|nextjs|bash|powershell)\s+(?:code|script|program|app|function|query|component)\b',
    r'\b(?:recipe\s+for|how\s+to\s+(?:cook|bake|prepare)\s+(?:a\s+)?(?:cake|pizza|pasta|chicken|steak|bread|cookies|soup))\b',
    r'\b(?:bitcoin|ethereum|cryptocurrency|crypto\s+trading|forex\s+trading|stock\s+market\s+picks|sports\s+betting|casino|gambling)\b',
    r'\b(?:who\s+is\s+the\s+(?:president|prime\s+minister|king|queen)|what\s+is\s+the\s+capital\s+of|who\s+won\s+the\s+(?:world\s+cup|super\s+bowl|election|oscars))\b',
    r'\b(?:solve\s+this\s+(?:math|algebra|calculus|geometry|homework)|homework\s+assignment|write\s+my\s+homework)\b',
    r'\b(?:dating\s+advice|relationship\s+advice|astrology\s+sign|tarot\s+reading|medical\s+diagnosis\s+for\s+my)\b',
    r'\b(?:scrie(?:-mi)?\s+(?:o\s+poezie|o\s+poveste|un\s+eseu|cod\s+python|o\s+glumă|o\s+gluma)|spune(?:-mi)?\s+o\s+glum[aă]|rețetă\s+de|reteta\s+de)\b',
    r'\b(?:напиши\s+(?:стих|поэму|рассказ|эссе|шутку|анекдот|код\s+на|рецепт)|расскажи\s+(?:анекдот|шутку|сказку))\b',
    r'\b(?:escribe\s+(?:un\s+poema|un\s+cuento|un\s+chiste|una\s+receta|código\s+en)|cuéntame\s+un\s+chiste)\b',
)
_OFF_TOPIC_RES = tuple(re.compile(p, re.I) for p in _OFF_TOPIC_PATTERNS)

_MAINTENANCE_DOMAIN_RE = re.compile(
    r'\b(?:unit|truck|trailer|reefer|case|cases|fleet|driver|group|agent|shift|shop|dealer|repair|repairs|maintenance|mechanic|'
    r'issue|issues|problem|symptom|diagnos\w*|check|checks|inspect\w*|replace\w*|fix|fixed|solved|resolution|history|records?|'
    r'part|parts|manual|spn|fmi|dtc|fault|code|codes|alarm|error|warning|brake|brakes|air|leak|leaking|tire|tires|tyre|wheel|rim|'
    r'hub|axle|stud|lug|bearing|seal|gasket|gladhand|chamber|airbag|suspension|spring|shock|steering|engine|motor|turbo|boost|'
    r'coolant|antifreeze|radiator|water\s+pump|thermostat|overheat\w*|oil|fuel|diesel|injector|filter|davco|pump|def|dpf|scr|nox|'
    r'regen|exhaust|muffler|battery|alternator|starter|voltage|wiring|harness|fuse|relay|sensor|abs|light|lights|lamp|door|hinge|'
    r'latch|panel|roof|floor|windshield|glass|mirror|fifth\s+wheel|kingpin|landing\s+gear|mudflap|bumper|clutch|transmission|'
    r'gearbox|differential|driveline|u-joint|compressor|condenser|evaporator|refrigerant|freon|setpoint|temp|temperature|'
    r'thermo\s+king|carrier|freightliner|cascadia|kenworth|peterbilt|volvo|mack|international|cummins|detroit|paccar|bendix|wabco|'
    r'meritor|haldex|hendrickson|davco|kurtex|board|open|closed|done|reported|active|missed|priority|urgent|cost|vendor|towing|'
    r'spend|amount|expensive|under|over|below|above|analytics|statistics|stats|frequent|frequently|invoice|invoices|receipt|receipts|payment|payments|shipping|freight|logistics|transportation|proof|proofs|dollar|usd|'
    r'roadside|breakdown|breakdowns|broke|crank|start|idle|stall|smoke|noise|vibration|crack|cracked|broken|worn|damaged|bent|loose|missing|'
    r'camion|remorc[aă]|frân[aă]|frana|motor|anvelop[aă]|roat[aă]|jant[aă]|scurgere|pierde|aer|ulei|antigel|baterie|pies[aă]|caz|cazuri|defect|'
    r'грузовик|прицеп|рефрижератор|тормоз\w*|двигател\w*|колес\w*|шин\w*|диск|утечк\w*|воздух\w*|масл\w*|антифриз|аккумулятор|ошибк\w*|ремонт\w*|кейс\w*|заявк\w*|юнит|'
    r'camión|camion|remolque|freno|frenos|neumático|llanta|fuga|aceite|refrigerante|batería|pieza|avería|falla|caso|casos|reparación)\b',
    re.I,
)

_SHORT_FOLLOWUP_RE = re.compile(
    r'^(?:ok|okay|yes|no|sure|thanks|thank\s+you|thx|got\s+it|understood|continue|more|why|how|what\s+else|and\s+then|'
    r'find\s+me|show\s+me|list\s+them|all\s+of\s+them|for\s+it|for\s+this|are\s+you\s+sure|u\s+sure|da|nu|mersi|mulțumesc|'
    r'да|нет|спасибо|понял|sí|gracias)[\s?.!]*$',
    re.I,
)

_GREETING_HELP_RE = re.compile(
    r'^(?:hi|hello|hey|yo|sup|good\s+(?:morning|afternoon|evening)|salut|bun[aă]|привет|здравствуйте|hola|buenos\s+d[ií]as|'
    r'help|what\s+can\s+you\s+do|who\s+are\s+you|how\s+does\s+this\s+work)[\s!.,?]*$',
    re.I,
)


def is_out_of_scope_query(message, history_text='', has_media=False):
    """Return True when a user prompt is outside Kurtex fleet maintenance & operations scope."""
    text = _normalize(message)
    if not text:
        return False
    if _GREETING_HELP_RE.match(text):
        return False
    has_domain_in_msg = bool(_MAINTENANCE_DOMAIN_RE.search(text))
    # Explicit jailbreak / off-topic patterns
    for rx in _OFF_TOPIC_RES:
        if rx.search(text):
            # Block unless clearly asking about a truck/trailer/reefer fault without jailbreak
            if 'ignore' in text or 'disregard' in text or 'forget' in text or 'jailbreak' in text or 'act as' in text or 'pretend' in text:
                return True
            if not has_domain_in_msg:
                return True
    if has_media or has_domain_in_msg:
        return False
    # Check for unit-number-like alphanumeric token (e.g. "308", "R376", "T-104")
    if re.search(r'\b(?:[rt]?\d{2,5}|unit\s*\w+)\b', text, re.I):
        return False
    if _SHORT_FOLLOWUP_RE.match(text):
        return False
    # If currently in an active maintenance conversation and message is a short follow-up (< 45 chars), allow it
    if history_text and _MAINTENANCE_DOMAIN_RE.search(history_text) and len(text) < 48:
        return False
    # Otherwise, if the prompt has 1+ words and zero maintenance/fleet terms, it is out of scope
    words = re.findall(r'[\w-]{2,}', text)
    if len(words) >= 1:
        return True
    return False


def out_of_scope_refusal(language='en'):
    refusals = {
        'ro': 'Sunt **Kurtex Maintenance AI** și sunt restricționat exclusiv la **mentenanța flotei (camioane, remorci, reefere), istoricul cazurilor pe unități, diagnosticarea simptomelor, coduri de eroare și manualul de piese**. Te rog introdu un număr de unitate, un cod de eroare (SPN/FMI) sau un simptom tehnic.',
        'ru': 'Я — **Kurtex Maintenance AI** и работаю исключительно по вопросам **технического обслуживания автопарка (грузовики, прицепы, рефрижераторы), поиска истории кейсов по юнитам, диагностики симптомов, кодов ошибок и каталога запчастей**. Пожалуйста, укажите номер юнита, код ошибки или симптом неисправности.',
        'es': 'Soy **Kurtex Maintenance AI** y estoy restringido exclusivamente al **mantenimiento de flota (camiones, remolques, reefers), historial de casos por unidad, diagnóstico de síntomas, códigos de falla y manual de partes**. Por favor ingresa un número de unidad, código de falla o síntoma mecánico.',
        'en': 'I am **Kurtex Maintenance AI** and I am strictly limited to **fleet maintenance, unit case history, symptom diagnostics, fault codes, parts manual lookups, and active breakdown operations**. I cannot answer questions outside fleet maintenance.\n\n- Enter a **Unit #** (e.g. `Unit 308`, `R376`) to search recorded maintenance cases.\n- Describe a **symptom** (e.g. `air leak at gladhand`, `hub cap leak`, `no crank`) to search matching repairs and checks.\n- Paste a **fault code** (`SPN / FMI` or reefer alarm) to decode and triage.',
    }
    return refusals.get(language, refusals['en'])
