# Michaela Laptop Price Agent

A free, GitHub-hosted price watcher for a school laptop powerful enough for Blender and Maya.

## What this is

This is a practical free agent that checks a small watchlist of laptop product pages once per day, scores the listings against Blender/Maya-oriented specs, saves the results, and opens a GitHub Issue if a price falls at or below your target.

It is intentionally not a full web-wide search engine. Truly searching all stores reliably usually requires a paid search/shopping API. This free version is best for watching product pages from stores such as Best Buy, Micro Center, Lenovo, Dell, B&H, Walmart, Costco, or Amazon after you paste the URLs into `config.yaml`.

## Good target specs for Michaela

For Blender/Maya school animation work, start with:

- NVIDIA RTX 4070 / RTX 5070 minimum
- RTX 4080 / RTX 5080 if the price is good
- 32 GB RAM minimum; 64 GB preferred
- 1 TB SSD minimum; 2 TB preferred
- 16-inch screen is a good balance; 18-inch is powerful but heavy

## Free hosting

Use a public GitHub repository. GitHub Actions is free for standard GitHub-hosted runners in public repositories. The workflow runs every morning and can also be run manually from the Actions tab.

## Setup steps

1. Create a new public GitHub repository, for example `michaela-laptop-price-agent`.
2. Upload these files to the repository.
3. Open `config.yaml`.
4. Replace the example URLs with real laptop product URLs you are considering.
5. Set `max_total_price` for each laptop.
6. Commit the changes.
7. Go to the **Actions** tab and run **Michaela Laptop Price Watch** manually once.
8. Watch the repository so GitHub emails you when the agent opens an alert issue.

## How alerts work

When the agent finds a product that appears to meet the specs and is at/below your target price, it creates a GitHub Issue with the price, URL, detected GPU/RAM/SSD, and reasons.

## Notes

Retailer pages change often. Some pages hide prices behind JavaScript or block automated requests. If a URL fails, try another retailer page for the same laptop.

Keep the watchlist small and the schedule modest. This is meant for personal deal tracking, not heavy scraping.
