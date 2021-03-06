"""Scrape court information from the PA's Unified Judicial System portal."""
import random
import time
from dataclasses import dataclass

import numpy as np
import simplejson as json
from loguru import logger
from phl_courts_scraper.portal import UJSPortalScraper
from selenium import webdriver
from tryagain import retries
from webdriver_manager.chrome import ChromeDriverManager

from . import DATA_DIR


@dataclass
class CourtInfoByIncident:
    """Court information for shooting incidents scraped from the
    PA's Unified Judicial System."""

    debug: bool = False

    def __post_init__(self):
        self._init_scraper()

    def _init_scraper(self):

        # Initialize the driver in headless mode
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        self.driver = webdriver.Chrome(ChromeDriverManager().install(), options=options)

        # Initialize the scraper
        self.scraper = UJSPortalScraper(self.driver)

    @property
    def path(self):
        return DATA_DIR / "raw" / "scraped_courts_data.json"

    def get(self):
        """Get the shooting victims data, either loading
        the currently downloaded version or a fresh copy."""

        return json.load(self.path.open("r"))

    def merge(self, data):
        """Merge courts data."""

        # Load raw courts data and existing dc keys
        courts = self.get()
        existing_dc_keys = [key for key in courts.keys() if len(courts[key])]

        if self.debug:
            logger.debug("Merging in court case information")

        out = data.copy()
        return out.assign(
            has_court_case=lambda df: np.select(
                [
                    df.dc_key.astype(float)
                    .astype(int)
                    .astype(str)
                    .isin(existing_dc_keys)
                ],
                [True],
                default=False,
            )
        )

    def update(
        self, shootings, sleep=7, chunk=None, dry_run=False, min_sleep=30, max_sleep=120
    ):
        """Scrape the courts portal."""

        # Load existing courts data
        courts = self.get()
        existing_dc_keys = list(courts.keys())

        # Trim shootings to those without cases
        N = len(shootings)
        if self.debug:
            logger.info(f"Scraping info for {N} shooting incidents")

        # Save new results here
        new_results = {}

        def cleanup():
            self.driver.close()
            logger.info("Retrying...")

        @retries(
            max_attempts=15,
            cleanup_hook=cleanup,
            pre_retry_hook=self._init_scraper,
            wait=lambda n: min(min_sleep + 2 ** n + random.random(), max_sleep),
        )
        def _call(i):

            if self.debug and i % 25 == 0:
                logger.debug(i)
            dc_key = shootings.iloc[i]["dc_key"]

            # Some DC keys for OIS are shorter
            if len(dc_key) == 12:

                # Scrape!
                scraping_result = self.scraper(dc_key[2:])

                # Save those with new results
                if scraping_result is not None:
                    new_results[dc_key] = scraping_result.to_dict()["data"]

                # Sleep!
                time.sleep(sleep)

        # Loop over shootings and scrape
        try:
            for i in range(N):
                _call(i)

        except Exception as e:
            logger.info(f"Exception raised: {e}")
        finally:
            if self.debug:
                logger.debug(f"Done scraping: {i+1} DC keys scraped")

            # Save
            if not dry_run:
                if chunk is None:
                    filename = "scraped_courts_data.json"
                else:
                    filename = f"scraped_courts_data_{chunk}.json"
                json.dump(new_results, (DATA_DIR / "raw" / filename).open("w"))
