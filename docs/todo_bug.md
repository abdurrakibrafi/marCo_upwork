Approve করার আগে দুইটা সমস্যা ঠিক করে দাও:

## ১. `"overs"` field-এর key ভুল হয়ে গেছে
আগে যে raw commentary sample verify করা হয়েছিল সেটা ছিল:
```json
{"post": "Deepti Sharma to Amy Jones", "runs": "0", "overs": "39.6", "ended": "true"}
```
এখানে over number-এর জন্য raw key হলো **`"overs"`** (with 's')। কিন্তু তোমার নতুন কোডে লেখা হয়েছে:
```python
"overs": item.get("over", ""),
```
এটা ভুল key (`"over"`, singular) পড়ছে — যেটা raw data-তে নেই, ফলে এই field খালি স্ট্রিং হয়ে যাবে, যেটা আগে ঠিকই কাজ করছিল। এটা ঠিক করে দাও:
```python
"overs": item.get("overs", ""),
```

## ২. `"iswicket"` এবং `"over_ended"` — এই দুটো key সত্যিই raw response-এ verify করা হয়েছে কিনা কনফার্ম করো

প্ল্যানে লেখা "Based on our live API verification" — কিন্তু exact output দেখানো হয়নি। নিশ্চিত করার জন্য আবার এই কমান্ডটা রান করে wicket হওয়া একটা বলের পুরো raw commentary object দেখাও:

```bash
docker exec mysportsnest_backend python manage.py shell -c "
from apps.sports_apis.services.statpal import statpal_service
r = statpal_service.get_cricket_live()
data = r.get('data', {})
categories = data.get('scores', {}).get('category', [])
if isinstance(categories, dict):
    categories = [categories]
for cat in categories:
    matches = cat.get('match', [])
    if isinstance(matches, dict):
        matches = [matches]
    for m in matches:
        comm = m.get('commentaries', {})
        entries = comm.get('commentary', [])
        if isinstance(entries, dict):
            entries = [entries]
        for e in entries:
            print(e)
"
```

এই আউটপুট থেকে সরাসরি দেখাও:
- `iswicket` নামের key সত্যিই আছে কিনা, এবং তার ভ্যালু ফরম্যাট (`"True"`/`"False"` স্ট্রিং, নাকি boolean, নাকি অন্য কিছু যেমন `"1"`/`"0"`)
- `over_ended` নামের কোনো key আদৌ raw data-তে আছে কিনা — যদি না থাকে, তাহলে এই key ব্যবহার না করে শুধু আগের মতো `item.get('ended', 'false')` রাখো, নতুন করে অস্তিত্বহীন key যোগ করার দরকার নেই

এই ভেরিফিকেশনের raw output আমাকে দেখাও, তারপর সেই অনুযায়ী কোড ঠিক আছে কিনা confirm করে approve করব।