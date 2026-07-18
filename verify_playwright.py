from playwright.sync_api import sync_playwright
import os
import time

def verify():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        csp_violations = []
        def handle_console(msg):
            text = msg.text.lower()
            if "content-security-policy" in text or "csp" in text or "refused to execute inline" in text or "violates the following content security policy" in text:
                csp_violations.append(msg.text)
            print(f"Browser Console: {msg.text}")
            
        page.on("console", handle_console)
        
        print("Testing index.html login...")
        page.goto('http://localhost:8765/')
        page.wait_for_selector('#login-overlay', state='visible')
        page.fill('#token-input', 'demo-token-123')
        page.click('.btn-login')
        page.wait_for_selector('#login-overlay', state='hidden')
        
        print("Testing dashboard.html login...")
        page.context.clear_cookies()
        page.goto('http://localhost:8765/dashboard.html')
        page.wait_for_selector('#login-overlay', state='visible')
        page.fill('#token-input', 'demo-token-123')
        page.click('.btn-login')
        page.wait_for_selector('#login-overlay', state='hidden')
        
        print("Testing 'Load Repository History' button...")
        page.evaluate('window.latestRun = { repo_url: "https://github.com/test/repo" };')
        page.click('#repo-history-btn')
        time.sleep(2)
        
        if csp_violations:
            print("CSP VIOLATIONS FOUND:")
            for v in csp_violations:
                print(v)
            print("FAIL")
        else:
            print("Zero CSP violations found. PASS")
        
        browser.close()

if __name__ == "__main__":
    verify()
