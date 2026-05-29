import httpx
from archguard.alerting.trend_detector import TrendAlert

async def send_slack_alert(webhook_url: str, alerts: list[TrendAlert]) -> None:
    if not alerts:
        return
        
    blocks = [{
        "type": "section",
        "text": {
            "type": "mrkdwn", 
            "text": f"🏗 *ArchGuard Trend Alert*\n" + "\n".join(f"• {a.message}" for a in alerts)
        }
    }]
    
    async with httpx.AsyncClient() as client:
        await client.post(webhook_url, json={"blocks": blocks})

async def send_generic_webhook(url: str, alerts: list[TrendAlert]) -> None:
    if not alerts:
        return
        
    payload = {
        "alerts": [
            {
                "message": a.message, 
                "metric": a.metric, 
                "module": a.module,
                "direction": a.direction,
                "delta": a.delta
            } for a in alerts
        ]
    }
    
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)
