Hi Farvez
Thanks for explaining the limitations of TheSportsDB. I understand that certain sports and
lower-tier leagues have limited coverage, and I'm comfortable adjusting our supported sports
where necessary.
For example, I'm completely fine with removing sports such as Handball and Volleyball if reliable
data isn't available. I'd rather focus on delivering a high-quality experience for our core
supported sports.
However, my understanding is that StatPal should provide us with up-to-date fixtures, results,
standings, player rosters and player statistics for our supported leagues. I'd like StatPal to
remain our primary sports data provider, with TheSportsDB serving as both a secondary data
provider and a supplementary media source.
I've also been testing the latest version of MySportsNest and wanted to consolidate my
outstanding feedback into one message. There are still several issues, particularly with news
retrieval, fixtures, standings and the overall Nest functionality.
I've included specific examples below to help identify the problems and clarify the expected
behaviour.
1. Nest Feed – Not Updating Across All Followed Entities
The Nest Feed is still not functioning as intended.
I currently have 10+ teams in my Nest, many of which are actively playing and should have
numerous relevant news stories published daily. However, only one or two teams appear to be
receiving regular article updates.
In fact, as of this evening (October 14), I have not seen a single new story populate my Nest
Feed throughout the entire day.
Expected functionality:
● The backend should automatically poll ALL stored news websites and sources every
15–30 minutes.
● Retrieve newly published articles relevant to our supported teams, leagues and athletes.
● Match articles to their corresponding entities.
● Automatically populate users' Nest Feeds with new stories from ALL their followed
entities.
● Display the newest stories first.
● Prevent duplicate articles from appearing.
● Run this process automatically in the backend, regardless of whether users have the app
open.
● Respect source-specific rate limits where applicable.
The intention is for the Nest Feed to feel like a constantly active, personalized sports news feed,
rather than showing occasional updates from only one or two teams.
2. Individual Entity Feeds – News Not Refreshing
This issue also extends to individual entity pages, including entities that are already followed
and those that are not.
Two examples from my testing:
Toronto Blue Jays (Already in My Nest)
● When I search for the Toronto Blue Jays and open their individual entity page, the most
recent article displayed is dated September 15.
● There have obviously been numerous stories published since then, but none are
appearing.
Manchester United (Not in My Nest)
● When I search for Manchester United and open their entity page, the most recent article
displayed is dated September 23.
● Again, the feed is significantly outdated.
The backend should refresh news for all supported entities approximately every 15–30 minutes,
regardless of whether someone has added that entity to their Nest.
Please investigate whether this is related to news ingestion, source polling, entity mapping,
caching, or articles not being correctly saved and delivered to the feeds.
Both individual entity feeds and the main Nest Feed should use the same updated article data.
3. Nest Feed – Missing Article Thumbnails
Currently, the Nest Feed displays article headlines and summaries, which is great, but no article
images or thumbnails are appearing.
I'd like the feed to incorporate the original article's thumbnail or featured image whenever
available.
The goal is to make the Nest Feed visually engaging, with a combination of headlines,
summaries and relevant images.
If an article doesn't have an available image, it can simply display without one.
4. Duplicate Teams and Missing Logos
I've noticed that when searching for NHL teams individually, logos are now appearing, which is
an improvement.
However, there seem to be duplicate team records appearing in search results.
For example:
● One version of a team displays its logo.
● Another version of the same team appears without a logo.
Additionally, logos are still missing when viewing fixtures and standings.
Please investigate the duplicate entity records and ensure the correct team profiles and logos
are consistently used throughout the application.
Given your explanation regarding TheSportsDB's strict name matching, I also want to ensure we
have proper entity mapping between StatPal and TheSportsDB, preferably using stable provider
IDs rather than relying solely on team names.
The same team should not appear as two separate entities simply because the providers use
slightly different naming conventions.
5. Fixtures – Previous Matches and Results Missing
Previous fixtures are not consistently displayed across different teams and sports.
Some entities show previous matches and their final scores, while others only show upcoming
games.
Every team's fixtures section should consistently display:
● Previous matches.
● Final scores and results.
● Upcoming scheduled matches.
● The ability to click a completed match to view its available statistics and match details.
This should work consistently across all retained supported sports, wherever the underlying
data is available.
StatPal should be the primary source for this information, with TheSportsDB used as a fallback
where necessary.
6. Fixtures – Chronological Ordering
The fixture ordering also needs to be adjusted.
When opening a team's fixtures section, it should automatically position the user at the current
date or closest upcoming fixture.
For example, if today is Thursday:
● Thursday's fixture (if applicable) should be immediately visible.
● Scrolling downward should display upcoming games in chronological order (Saturday,
Tuesday, etc.).
● Scrolling upward should display previous games, including their final scores and results.
The user shouldn't have to scroll through numerous dates to locate the next relevant game.
7. Fixtures – Full-Season Schedules
Currently, many teams, particularly in soccer and football, only display approximately one week
of upcoming fixtures.
I would like the application to display the entire available season schedule, rather than only the
next seven days.
StatPal should be our primary source for retrieving these schedules, with TheSportsDB serving
as a backup where StatPal information is missing or incomplete.
Ideally, the backend should retrieve and store the complete available season schedule for each
supported team.
If a league has not yet released its entire schedule, the application should display all currently
confirmed fixtures and retrieve additional fixtures as they become available.
8. Automatic Daily Fixture Refresh
Once the full-season schedule has been retrieved, it should not remain static.
Match dates and kickoff times can change because of tournaments, postponements,
broadcasting adjustments and other scheduling changes.
Please implement an automatic backend refresh at least once daily to check for any fixture
changes.
Any updated dates or times should automatically be reflected throughout the application,
including individual entity pages and the Nest Calendar.
This process should run automatically without requiring users to refresh their app or remove and
re-add teams to their Nest.
9. Nest Homepage – Entity Navigation Still Not Working
The Nest function on the homepage is still not working correctly.
When I select the Nest, my followed entities appear arranged around it, which is correct.
However, clicking an individual entity does not navigate to its corresponding entity page.
Each team, league or athlete displayed around the Nest should be clickable and take the user
directly to that entity's page.
This remains an outstanding issue from my previous feedback.
10. Nest Calendar – Missing Fixtures for Followed Teams
The Nest Calendar is also not consistently displaying upcoming games for entities I've added to
my Nest.
For example, I have the Toronto Maple Leafs added to my Nest using the correct team profile
(the one displaying the logo).
However, their upcoming games are not appearing in my Nest Calendar.
The calendar should automatically populate with upcoming fixtures for all followed entities,
where applicable.
Please investigate whether this is related to duplicate team records, incorrect entity IDs, fixture
retrieval, or synchronization between followed entities and the Nest Calendar.
If a team's fixtures are available through StatPal or TheSportsDB, those fixtures should also be
available to the Nest Calendar when that team is followed.
11. League Standings – Previous Screen Recording Feedback
The standings still do not reflect the layouts and formatting I demonstrated in my previous
screen recording.
In that recording, I provided examples of how the standings should look for each respective
league.
Please revisit the recording and implement the appropriate league-specific standings layouts,
including:
● Correct columns and statistics.
● Appropriate league structures.
● Divisions and conferences where applicable.
● Correct rankings and formatting.
● Team logos alongside team names.
The goal is for each league's standings to reflect its actual structure, rather than using a generic
layout across every sport.
StatPal should be our primary source for standings data, with TheSportsDB used as a fallback
where necessary.
12. Consistency Across the Application
One recurring issue I'm noticing is that functionality differs depending on which entity or sport
I'm viewing.
For example, one team may have its logo, previous results or updated articles displaying
correctly, while another team does not.
Please ensure that the fixes above are tested across multiple entities and sports, rather than
verifying that they work for only one or two examples.
This includes checking both newly added entities and entities that have been stored in a user's
Nest for some time.
I want to ensure that existing users and their previously saved entities continue receiving
updated information without having to delete and re-add them.
13. Data Provider Structure – StatPal and TheSportsDB
Following your explanation regarding TheSportsDB, I want to clarify how I'd like us to structure
our sports data going forward.
As mentioned earlier, I'm comfortable removing sports such as Handball and Volleyball if reliable
coverage isn't available.
For the sports we retain, I'd like us to establish the following provider hierarchy, subject to the
endpoints and coverage available through our subscriptions.
StatPal – Primary Data Provider
● Fixtures and full-season schedules.
● Live scores and completed match results.
● League standings.
● Team rosters.
● Player statistics.
● Team and player information.
TheSportsDB – Secondary Data Provider
● Backup source for fixtures, schedules, results, standings and rosters whenever StatPal
information is missing or unavailable.
● Team badges and player headshots.
● Highlights and supplementary media.
Where StatPal has accurate information available, it should always take priority.
TheSportsDB should automatically supplement missing information rather than overwrite
accurate StatPal data with potentially outdated records.
I also want to ensure we are correctly matching entities between the two providers so that the
same team, league or athlete is consistently recognized throughout the application.
This should help prevent duplicate profiles and ensure fixtures, standings, logos, news articles
and calendar entries are all associated with the correct entity.
I would also prefer that we avoid relying on Wikipedia scraping for rosters if the required
information is already available through StatPal.
Please confirm whether this is how our current backend operates and, if not, what changes are
required to implement this structure.
Final Note – Priorities Going Forward
The biggest priority for me right now is getting the core functionality working consistently,
particularly:
1. News retrieval and automatic backend polling every 15–30 minutes across all configured
news sources.
2. Ensuring both the Nest Feed and individual entity feeds display fresh, relevant stories.
3. Correct fixture retrieval, full-season schedules, previous results and chronological
ordering.
4. Proper synchronization between followed entities and the Nest Calendar.
5. Resolving the outstanding navigation, duplicate entity, logo and standings issues.
6. Establishing StatPal as our primary sports data provider, with TheSportsDB serving as
the secondary provider and media enrichment source.
The overall goal is for MySportsNest to feel like an active, reliable and continuously updated
sports platform.
I understand that third-party data providers have limitations, and I'm comfortable adjusting our
supported sports where necessary. However, I want to ensure we're making full use of the
information already available through StatPal and TheSportsDB before accepting missing
information as a provider limitation.
Please review all the points above and provide an update on:
● Which issues have been identified and reproduced.
● Which fixes are currently being implemented.
● Whether any issues are caused by our backend rather than provider limitations.
● Which requirements cannot currently be achieved with our existing API subscriptions.
● The expected timeline for addressing the outstanding issues.
I'd also appreciate it if the team could test these fixes across multiple supported sports and
entities before sending the next APK, so we can avoid repeatedly encountering the same issues
between builds.
Thank you!
Marco