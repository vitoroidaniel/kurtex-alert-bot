"""Learning review API, restricted to existing trainer roles."""
from flask import jsonify, request, session

def register_learning_routes(app,learning,chats,user_key,is_trainer,load_cases,is_testing,serialized):
    @app.route('/api/ai/learning',methods=['GET'])
    def learning_list():
        if not session.get('user'):return jsonify(error='unauthorized'),401
        if not is_trainer():return jsonify(error='forbidden'),403
        status=request.args.get('status','pending')
        if status not in ('pending','approved','rejected'):return jsonify(error='Invalid status'),400
        try:offset=max(0,int(request.args.get('offset',0)))
        except ValueError:return jsonify(error='Invalid offset'),400
        return jsonify(learning.list(status,offset))

    @app.route('/api/ai/learning/import',methods=['POST'])
    def learning_import():
        if not session.get('user'):return jsonify(error='unauthorized'),401
        if not is_trainer():return jsonify(error='forbidden'),403
        count=0
        for owner,items in chats.all_chats():
            for chat in items:learning.capture_chat(owner,chat);count+=1
        case_count=learning.capture_cases([c for c in load_cases() if not is_testing(c)])
        return jsonify(ok=True,chats=count,cases=case_count)

    @app.route('/api/ai/learning/<ident>',methods=['PATCH'])
    def learning_review(ident):
        if not session.get('user'):return jsonify(error='unauthorized'),401
        if not is_trainer():return jsonify(error='forbidden'),403
        data=request.get_json(silent=True) or {}
        tags=data.get('tags') or []
        if not isinstance(tags,list):return jsonify(error='Tags must be a list'),400
        try:
            found=learning.review(ident,str(data.get('status','')),str(data.get('content','')).strip(),str(data.get('title','Maintenance lesson')),[str(t)[:50] for t in tags],user_key())
        except ValueError as error:return jsonify(error=str(error)),400
        return (jsonify(ok=True),200) if found else (jsonify(error='Lesson not found'),404)

    @app.route('/api/ai/chats/<chat_id>/feedback',methods=['POST'])
    @serialized
    def chat_feedback(chat_id):
        chat=next((c for c in chats.read(user_key()) if c.get('id')==chat_id),None)
        if not chat:return jsonify(error='Chat not found'),404
        data=request.get_json(silent=True) or {};correction=str(data.get('correction','')).strip()
        if not 10<=len(correction)<=12000:return jsonify(error='Describe the correction or confirmed outcome in 10-12000 characters.'),400
        messages=chat.get('messages',[]);wanted=str(data.get('message_id',''))
        found=next(((i,m) for i,m in enumerate(messages) if m.get('role')=='assistant' and str(m.get('id') or i)==wanted),None)
        if not found:return jsonify(error='Answer not found. Reopen the conversation and try again.'),404
        index,message=found;question=messages[index-1].get('content','') if index else ''
        evidence='Agent question:\n'+question+'\n\nPrevious AI answer (unverified):\n'+message.get('content','')+'\n\nAgent correction / reported outcome (awaiting review):\n'+correction
        ident=learning.capture('feedback:'+user_key()+':'+chat_id+':'+wanted,'correction',chat.get('title','Maintenance correction'),evidence)
        return jsonify(ok=True,id=ident,status='pending'),201
