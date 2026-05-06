@echo off
cd /d "C:\dev\Harmonic"
python run_pipeline.py collect --collectors hacker_news,arxiv,rss_feeds,news_api
python scripts/red-team-hybrid/freshness_watchdog.py --json > "C:\dev\Harmonic\artifacts\keepalive\%DATE:~10,4%-%DATE:~4,2%-%DATE:~7,2%.json"
