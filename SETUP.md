# Fare Watch: setup guide

Allow about 30–40 minutes. You only do this once. Everything here is free.

**How it works:** every 2 hours, GitHub runs `fare_watch.py` in the cloud. The script gets fares from Melbourne to your destinations, scores each one out of 100, saves the results for the app, and emails you any fare scoring 80 or more. Your phone and laptop don't need to be on.

> [Unverified] Free-tier limits, menu names, and button labels below come from these services' published documentation and may have changed. If a screen looks different, look for the closest match.

---

## 1. Get a free fare-data token (Travelpayouts)

1. Go to **travelpayouts.com** and create a free account.
2. Open **Tools → API** (or go to `travelpayouts.com/programs/100/tools/api`).
3. Copy your **API token** and keep it somewhere safe for step 4.

Note: these prices come from a cache of recent searches made by other travellers on Aviasales. They are not live airline quotes, so always check the price before you pay.

## 2. Make a Gmail app password (for the alert emails)

1. Go to **myaccount.google.com → Security**. Turn on **2-Step Verification** if it's off; it's required for the next step.
2. Search the Google Account page for **App passwords**.
3. Create one named `Fare Watch` and copy the 16-character password it shows you.

This password lets the script send email only. It isn't your normal Gmail password, and you can delete it any time.

## 3. Put the project on GitHub

Done. Claude uploaded the files to your **Fare-Watcher** repository.

## 4. Add your secret keys

In your repo: **Settings → Secrets and variables → Actions → New repository secret**. Add these four:

| Name | Value |
|---|---|
| `TRAVELPAYOUTS_TOKEN` | the token from step 1 |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password from step 2 |
| `ALERT_TO` | where alerts go (can be the same Gmail) |

## 5. Turn on the app and the checker

1. **Settings → Pages**. Under *Source*, choose **Deploy from a branch**, then branch `main`, folder `/ (root)`, and click **Save**.
   After a minute or two, your app's address appears at the top. It looks like `https://laurakellett09-hue.github.io/Fare-Watcher/`.
2. **Settings → Actions → General**. Under *Workflow permissions*, choose **Read and write permissions** and click **Save**. This lets the checker save results for the app.
3. Open the **Actions** tab. If GitHub asks, click **I understand… enable them**.

## 6. Connect the app to GitHub (once per phone)

The app needs a token so **Save settings** and **Check now** can work.

1. On GitHub: your profile picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Fill it in:
   - Name: `Fare Watch app`
   - Expiration: up to 1 year (set a reminder to renew it)
   - Repository access: **Only select repositories → Fare-Watcher**
   - Permissions → Repository permissions: **Contents: Read and write**, **Actions: Read and write**
3. Click **Generate token** and copy it.
4. Open your app address on your phone. Go to **Settings → Connect to GitHub**, paste the token, and check that the repository shows `laurakellett09-hue/Fare-Watcher`.

The token is saved only in that phone's browser. Anyone holding it can change your Fare-Watcher repo, so don't share it.

## 7. Pick your destinations and add the app to your home screen

1. In the app, open **Settings**. Add up to 6 destinations, set your date window, choose return and/or one-way, and pick preferred or avoided airlines. Then tap **Save settings**. A check starts automatically.
2. **Add to home screen**
   - iPhone (Safari): Share button → **Add to Home Screen**
   - Android (Chrome): ⋮ menu → **Add to Home screen** / **Install app**

The app first shows clearly labelled **sample fares**. Real fares replace them after the first check, about 5 minutes after you save.

---

## How the score works (out of 100)

| Part | Points | What earns them |
|---|---|---|
| Price vs usual | 60 | 0 pts at or above the usual fare; full points at 40%+ cheaper |
| Airline | 15 | Preferred airline 15, neutral 11 (or 11 if you have no preferences), other 7.5 |
| Stops | 15 | Direct 15, one stop 9, two or more 3 |
| Flight time | 10 | Compared with the fastest option on that route that month |

**Usual fare** is the median price for that destination, trip type, and month. It's worked out from today's fares plus up to 45 days of saved history. [Inference] Scores get more reliable after the checker has run for a couple of weeks. Until there's enough data, fares are tagged *Little price history* and don't trigger emails.

[Inference] Roughly speaking, a direct flight on a neutral airline needs to be about 30% under the usual fare to reach 80.

You get an email only for **new** 80+ fares, or for ones that have dropped at least another 3% since you were last emailed about them.

## Changing things later

- **Destinations, dates, minimum score, airlines:** use the app's Settings.
- **Check frequency:** edit the `cron` line in `.github/workflows/check-fares.yml`. `17 */2 * * *` means minute 17 of every 2nd hour, in UTC.
- **Score weights:** edit `W_PRICE`, `W_AIRLINE`, `W_STOPS`, and `W_DURATION` near the top of `fare_watch.py`.

## If something isn't working

| What you see | What to check |
|---|---|
| No email ever arrives | Look in Spam. Check that the 4 secrets in step 4 are named exactly as shown. |
| App says a search failed | Open the **Actions** tab, click the latest run, and read the red error. A `401` or token error means `TRAVELPAYOUTS_TOKEN` is wrong. |
| "GitHub didn't accept the token" in the app | The step 6 token expired or is missing a permission. Make a new one. |
| Checks stopped running | [Unverified] GitHub can pause scheduled workflows on repos with no activity for 60 days. The checker's own commits should count as activity. If it pauses, open **Actions** and re-enable it. |
| Usage limits | [Unverified] GitHub Actions minutes are free for public repos. Travelpayouts' Data API limits aren't published in the docs I found. With 6 destinations × 6 months × 2 trip types, each check makes up to 72 requests. If you hit limits, use fewer months or destinations. |
