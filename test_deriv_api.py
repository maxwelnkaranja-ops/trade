
import asyncio
import aiohttp

# Your PAT token
PAT_TOKEN = "pat_2ce0c945f675cd335a9cdff70c45929e7f7f30394147e61e7b1d91dd8e227917"
# Current App ID
APP_ID = "33ABvw2NXwok58OBM2al0"
REST_BASE = "https://api.derivws.com"

async def test_api():
    headers = {
        "Authorization": f"Bearer {PAT_TOKEN}",
        "Deriv-App-ID": APP_ID,
        "Content-Type": "application/json",
    }

    print("Testing Deriv API with PAT token...")
    print(f"App ID: {APP_ID}")
    print()

    async with aiohttp.ClientSession() as session:
        # Test 1: Get accounts
        print("Test 1: Getting accounts...")
        try:
            async with session.get(
                f"{REST_BASE}/trading/v1/options/accounts",
                headers=headers,
            ) as resp:
                print(f"Status: {resp.status}")
                print(f"Headers: {dict(resp.headers)}")
                body = await resp.json()
                print(f"Response: {body}")
                print()

                if resp.status == 200:
                    accounts = body.get("data", [])
                    if accounts:
                        account_id = accounts[0].get("id") or accounts[0].get("account_id")
                        print(f"Account ID found: {account_id}")
                        
                        # Test 2: Get OTP
                        print("\nTest 2: Getting OTP WebSocket URL...")
                        async with session.post(
                            f"{REST_BASE}/trading/v1/options/accounts/{account_id}/otp",
                            headers=headers,
                        ) as resp2:
                            print(f"Status: {resp2.status}")
                            print(f"Headers: {dict(resp2.headers)}")
                            body2 = await resp2.json()
                            print(f"Response: {body2}")
                            
        except Exception as e:
            print(f"Error: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test_api())
