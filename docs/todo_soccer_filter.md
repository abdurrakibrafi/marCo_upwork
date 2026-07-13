# সমস্যা ও সমাধান — Soccer Live Match (has_live_stats: False) Filter করা
 
## সমস্যার বিবরণ (Diagnosis)
 
MySportsNest প্রজেক্টে soccer live matches এর `statistics` ও `events` ফিল্ড কিছু ম্যাচে খালি
(`[]`) আসছে। কারণ StatPal API নিজেই সেই ম্যাচগুলোর জন্য detailed stats/events data দেয় না —
এটা তাদের raw response এ একটা ফ্ল্যাগ দিয়ে আগে থেকেই জানিয়ে দেয়:
 
```json
"has_live_stats": "False"
```
 
উদাহরণ (StatPal soccer live match object):
```python
{
    'main_id': '2026071368174',
    'status': '76',
    'home': {'id': '2363758', 'name': 'San Juan', 'goals': '0'},
    'away': {'id': '2368082', 'name': 'Marin', 'goals': '0'},
    'events': None,
    'ht': {'home_goals': 2, 'away_goals': 1},
    'has_live_stats': 'False',   # <-- এইটাই মূল কারণ
    ...
}
```
 
এটা bug না — এটা কিছু নিচু-স্তরের league (যেমন "USA: Usl League Two") এর ম্যাচে StatPal-এর
data source limitation। এটার জন্য code fix করা যাবে না, বরং decision নেওয়া হয়েছে: **যেসব ম্যাচে
`has_live_stats: False`, সেগুলো frontend-এ একদমই দেখানো হবে না** — অর্থাৎ এগুলো
`LiveScore` টেবিলে save/update-ই করা হবে না।
 
## যা করতে হবে (Required Fix)
 
### ধাপ ১: soccer sync flow খুঁজে বের করা
 
`/app/apps/event/tasks.py` ফাইলে `update_soccer_live_scores_only()` ফাংশনটি দেখা গেছে, যেটা
সরাসরি নিজে কিছু করে না — বরং এটা `sync_statpal_data.delay()` নামের আরেকটা Celery task-কে
delegate করে। **`sync_statpal_data` ফাংশনটা খুঁজে বের করতে হবে** (সম্ভবত `/app/apps/event/tasks.py`
বা অন্য কোনো shared tasks ফাইলে থাকতে পারে):
 
```bash
grep -rn "def sync_statpal_data" /app/apps --include="*.py"
```
 
এই ফাংশনের ভেতরেই soccer-এর জন্য StatPal থেকে live match list আনা হয় এবং প্রতিটা match নিয়ে
`LiveScore.objects.update_or_create(...)` বা `get_or_create(...)` কল করা হয়।
 
### ধাপ ২: filter বসানো
 
`sync_statpal_data` (বা যেখানেই soccer match loop হয়) এর ভেতরে, প্রতিটা soccer match `m`
(StatPal raw match dict) নিয়ে লুপ করার সময়, `LiveScore.objects.update_or_create(...)` কল করার
ঠিক আগে এই চেক বসাতে হবে:
 
```python
# StatPal নিজেই বলছে এই ম্যাচের জন্য stats/events নেই → skip করো, DB তে save/update করো না
if str(m.get('has_live_stats', 'True')).strip().lower() == 'false':
    continue  # (বা for-loop এর syntax অনুযায়ী এর সমতুল্য 'skip this match')
```
 
**গুরুত্বপূর্ণ:** `.strip().lower() == 'false'` ব্যবহার করা হয়েছে কারণ StatPal এই ফিল্ডটা string
হিসেবে পাঠায় (`'False'`/`'True'`), Python বুলিয়ান হিসেবে না। তাই সরাসরি `if not m.get('has_live_stats')`
লিখলে কাজ করবে না (কারণ non-empty string সবসময় truthy হয়, এমনকি `'False'` স্ট্রিংও)।
 
### ধাপ ৩: আগে থেকে save হয়ে থাকা ম্যাচ পরিষ্কার করা (Cleanup — one-time)
 
কিছু ম্যাচ ইতিমধ্যে DB-তে `LiveScore` হিসেবে সেভ হয়ে গেছে যেগুলোর `has_live_stats: False`
(যেমন এই কথোপকথনে পাওয়া id=15743, "San Juan vs Marin")। নতুন sync চালু হওয়ার পর এগুলো
আর আপডেট হবে না ঠিকই, কিন্তু status='live' থাকা অবস্থাতেই থেকে যাবে (নতুন sync এসে বাদ দিলেও
পুরনো রেকর্ড DB-তে থেকে যাবে, delete হবে না, যতক্ষণ না কেউ সেটা explicitly delete করে)।
 
তাই একটা one-time cleanup script/management command চালানো উচিত, যেটা:
1. বর্তমানে `status='live'` থাকা সব soccer `LiveScore` রেকর্ড নিয়ে StatPal থেকে আবার চেক করবে
2. যাদের `has_live_stats: False` (অথবা যাদের external_id বর্তমান live feed-এ খুঁজে পাওয়া যায় না),
   সেগুলোকে either delete করবে অথবা status পরিবর্তন করে দেবে (যেমন `status='hidden'` বা
   সরাসরি delete — যেটা প্রজেক্টের business logic অনুযায়ী উপযুক্ত মনে হয়)
### ধাপ ৪: যাচাই (Verification)
 
fix করার পর:
```bash
docker exec mysportsnest_backend python manage.py shell -c "
from apps.score.models import LiveScore
print(LiveScore.objects.filter(id=15743).exists())
"
```
এটা `False` দেখানো উচিত (অর্থাৎ পরবর্তী sync cycle-এর পর এই ম্যাচ আর DB-তে থাকবে না,
এবং API response-এও আর আসবে না)।
 
## সংক্ষেপে (TL;DR for agent)
1. `sync_statpal_data` টাস্ক খুঁজে বের করো (grep দিয়ে)
2. soccer match loop-এ, `LiveScore` create/update করার আগে `has_live_stats == 'False'` (string check) হলে `continue` করে skip করো
3. একটা one-time cleanup command লিখে বর্তমানে থাকা `has_live_stats: False` ম্যাচগুলো DB থেকে সরাও
4. টেস্ট করে নিশ্চিত করো id=15743 আর ফিরে আসছে না