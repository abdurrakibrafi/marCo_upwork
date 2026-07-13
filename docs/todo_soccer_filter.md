# ফিক্স ১: Cricket ball-by-ball কমেন্ট্রি (সহজ)

`score/views.py`-তে cricket ball-by-ball বের করার সময় ভুল key ব্যবহার হচ্ছে:

```python
ball_by_ball = raw.get('comments', {}).get('Live', [])[-10:]
```

কিন্তু StatPal-এর raw response-এ আসল key হলো **`commentaries`**, `comments` না। এটা ঠিক করো:

```python
ball_by_ball = raw.get('commentaries', {}).get('Live', [])[-10:]
```

**গুরুত্বপূর্ণ:** ঠিক করার আগে `commentaries`-এর ভেতরের actual structure verify করে নাও (raw dict-এ `commentaries` key-এর ভ্যালু dict নাকি list, আর তার ভেতরে `'Live'` নামে sub-key সত্যিই আছে কিনা)। যদি structure ভিন্ন হয় (যেমন সরাসরি list, বা অন্য sub-key নাম), সেই অনুযায়ী কোড এডজাস্ট করো। এটা যাচাই করতে:

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
        if m.get('commentaries'):
            print(type(m['commentaries']), m['commentaries'])
            break
"
```

---

# ফিক্স ২: Tennis statistics (নতুন API কল লাগবে)

**সমস্যা:** এখন soccer-এর মতোই, tennis-এর জন্যও `get_tennis_live()` (endpoint: `https://statpal.io/api/v1/tennis/livescores`) কল করা হচ্ছে, যেটা শুধু বেসিক স্কোর দেয় (সেট স্কোর, সার্ভ, উইনার)। বিস্তারিত ম্যাচ statistics (winners, unforced errors, break points, ইত্যাদি) এই endpoint-এ নেই।

StatPal-এর কাছে এর জন্য আলাদা endpoint আছে:
```
GET https://statpal.io/api/v1/tennis/livestats
```

`statpal.py`-তে এটা ইতিমধ্যে আছে:
```python
def get_tennis_live_stats(self) -> dict:
    """Response root: live_stats → tournament → match[]"""
    return self._get(f"{self.base_v1}/tennis/livestats")
```

কিন্তু `score/views.py`-এর tennis detail flow-এ এই ফাংশনটা call করা হচ্ছে না — শুধু `get_tennis_live()`-এর data দিয়ে `_convert_tennis_stats` চালানো হচ্ছে, যেখানে `stats`/`period` structure নেই বলে statistics সবসময় খালি আসে।

## যা করতে হবে:

1. `score/views.py`-তে যেখানে tennis-এর জন্য `detail_raw`/match data বের করা হয়, সেখানে **আরেকটা কল যোগ করো**: `statpal_service.get_tennis_live_stats()`।
2. `live_stats → tournament → match[]` থেকে external_id/main_id মিলিয়ে সংশ্লিষ্ট ম্যাচের stats object বের করো (ঠিক যেভাবে soccer-এর জন্য `main_id`/`fallback_id` মিলিয়ে ম্যাচ খোঁজা হয়, একই প্যাটার্নে)।
3. এই stats object-টা `_convert_tennis_stats`-এ পাস করো, `get_tennis_live()`-এর match object-এর বদলে (অথবা দুটো merge করে পাঠাও, যদি `_convert_tennis_stats` উভয় সোর্স থেকে ডেটা নেয়)।
4. প্রথমে raw response দেখে `live_stats` object-এর আসল structure (`player` এর ভেতরে `stats`/`period` কী ফরম্যাটে আছে) verify করে নাও, তারপর `_convert_tennis_stats` ফাংশনের parsing logic সেই অনুযায়ী ঠিক আছে কিনা মিলিয়ে দেখো। যাচাই করতে:

```bash
docker exec mysportsnest_backend python manage.py shell -c "
from apps.sports_apis.services.statpal import statpal_service
r = statpal_service.get_tennis_live_stats()
print('success:', r.get('success'))
data = r.get('data', {})
tournaments = data.get('live_stats', {}).get('tournament', [])
if isinstance(tournaments, dict):
    tournaments = [tournaments]
for t in tournaments[:1]:
    matches = t.get('match', [])
    if isinstance(matches, dict):
        matches = [matches]
    for m in matches[:1]:
        print(m)
"
```

5. **Performance/rate-limit সতর্কতা:** এই নতুন `get_tennis_live_stats()` কলটা প্রতি tennis detail request-এ আলাদা API hit করবে (soccer-এর match-stats কলের মতোই যেটাতে আগে আমরা অনেক HTTP 404 দেখেছিলাম)। তাই এটা আগের মতোই cache করার কথা বিবেচনা করো (যেমন soccer-এর জন্য `cache_key = 'statpal_soccer_live_full'` ৩০ সেকেন্ডের জন্য cache করা হয়েছিল, tennis-এর জন্যও একই প্যাটার্নে ৩০-৬০ সেকেন্ডের cache যোগ করো), যাতে বারবার API rate limit বা delay-এর সমস্যা না হয়।

## যাচাই
ফিক্স করার পর, id=15748 বা 15747 (তোমার DB-তে থাকা লাইভ tennis ম্যাচ) দিয়ে detail endpoint হিট করে দেখো `statistics` array-তে এখন ডেটা আসছে কিনা।