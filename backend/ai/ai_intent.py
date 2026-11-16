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
 'history': ('similar case','similar cases','previous case','past case','case history','fleet history','historical cases',
             'cazuri similare','cazuri asemănătoare','cazuri asemanatoare','istoric cazuri','cazuri precedente',
             'похожие случаи','похожие заявки','история ремонтов','предыдущие случаи',
             'casos similares','casos anteriores','historial de casos'),
 'fault_code': ('fault code','error code','spn','fmi','dtc','obd','cod eroare','cod de eroare','cod defect',
                'код ошибки','код неисправности','ошибка spn','código de falla','codigo de falla','código de error','codigo de error'),
 'parts': ('part number','replacement part','parts manual','part #','număr piesă','numar piesa','cod piesă','cod piesa',
           'номер детали','артикул детали','запчасть','número de pieza','numero de pieza','repuesto'),
 'visual': ('what do you see','describe the image','describe this picture','what is visible',
            'ce vezi','descrie imaginea','ce se vede','что видно','опиши фото','что видишь',
            'qué ves','que ves','describe la imagen','qué se ve','que se ve'),
 'board_ops': ('[live board summary]','live board','active cases','active breakdowns','high priority',
               'unassigned','in shop','over $','cost over','on shift','summarize board','board summary',
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
    if '[live board summary]' in text or any(phrase in text for phrase in TASK_WORDS['board_ops']):
        return 'board_ops'
    for task in ('history','fault_code','parts','visual'):
        if any(phrase in text for phrase in TASK_WORDS[task]):return task
    if has_images:return 'image_inspection'
    return 'maintenance_question'


def needs_web_search(message, task, has_images=False):
    """Gate external web search to queries where technical/OEM snippets actually help."""
    if task in ('board_ops', 'visual', 'history'):
        return False
    text = _normalize(message)
    if len(text) < 8 and not has_images:
        return False
    if task in ('fault_code', 'parts'):
        return True
    explicit = ('manual', 'spec', 'torque', 'procedure', 'oem', 'diagram', 'wiring', 'search web', 'look up')
    if any(w in text for w in explicit):
        return True
    if re.search(r'\b(?:spn|fmi|dtc|[pbcu]\d{4})\b', text):
        return True
    # Only search on general diagnostic symptoms when substantial symptom terms exist
    symptom_hits = sum(1 for w in ('leak', 'brake', 'air', 'coolant', 'overheat', 'reefer', 'turbo', 'def', 'dpf', 'regen', 'no start', 'battery', 'alternator', 'abs', 'sensor', 'valve', 'compressor', 'smoke', 'alarm', 'error', 'code') if w in text)
    return symptom_hits >= 1


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
        'board_ops':'Use ONLY the supplied Live Board / Active Case data. Group cleanly by Unit #, Priority, Stage/Shop, and Cost; keep it scannable and omit filler or maintenance disclaimers.',
        'history':'Lead with matching fleet cases and confirmed recorded resolutions only. Omit generic troubleshooting unless asked.',
        'fault_code':'State exact code meaning if verified, make/model ambiguity, and the top 2-3 sharpest checks.',
        'parts':'Lead with verified part identification or state clearly that fitment is unconfirmed. Never invent OEM part numbers.',
        'visual':'Describe only visible evidence concisely. Do not turn a visual question into a full diagnostic report.',
        'image_inspection':'State visible findings first, what cannot be confirmed, and the top 1-3 next checks.',
        'video_inspection':'Cite sampled frame timestamps for visible findings, briefly separate driver audio if transcribed, and give the top 1-3 next checks.',
        'maintenance_question':'Answer the direct question in line 1, then give at most 3 high-signal bullets or next checks.'
    }
    return {'language':lang,'language_name':LANGUAGE_NAMES[lang],'task':task,
            'instructions':('Reply in '+LANGUAGE_NAMES[lang]+'. Preserve original fault codes, unit numbers, and part numbers verbatim. '
                'Write like a crisp, senior fleet maintenance dispatcher: zero fluff, zero greetings, never repeat the user question, never output empty headings or boilerplate disclaimers. '
                'FIRST LINE: direct answer or immediate safety action. '+sections[task]+' '
                'Keep replies compact (1-2 short sentences + up to 3 concise bullets) unless the user asks for deep detail.')}
