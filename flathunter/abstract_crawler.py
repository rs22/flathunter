"""Interface for webcrawlers. Crawler implementations should subclass this"""
import re
import logging
import requests
import selenium
import urllib.parse
import json
from time import sleep as sleep
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium import webdriver
from bs4 import BeautifulSoup
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import HardwareType, Popularity
from flathunter import proxies

class Crawler:
    """Defines the Crawler interface"""

    __log__ = logging.getLogger('flathunt')
    URL_PATTERN = None

    def __init__(self, config):
        self.config = config

    user_agent_rotator = UserAgent(popularity=[Popularity.COMMON._value_],
                                   hardware_types=[HardwareType.COMPUTER._value_])

    HEADERS = {
        'Connection': 'keep-alive',
        'Pragma': 'no-cache',
        'Cache-Control': 'no-cache',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': user_agent_rotator.get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;'
                  'q=0.9,image/webp,image/apng,*/*;q=0.8,'
                  'application/signed-exchange;v=b3;q=0.9',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-User': '?1',
        'Sec-Fetch-Dest': 'document',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    def configure_driver(self, driver_path, driver_arguments):
        chrome_options = Options()
        if driver_arguments is not None:
            for driver_argument in driver_arguments:
                chrome_options.add_argument(driver_argument)
        driver = webdriver.Chrome(executable_path=driver_path, options=chrome_options)
        return driver

    def rotate_user_agent(self):
        """Choose a new random user agent"""
        self.HEADERS['User-Agent'] = self.user_agent_rotator.get_random_user_agent()

    # pylint: disable=unused-argument
    def get_page(self, search_url, driver=None, page_no=None):
        """Applies a page number to a formatted search URL and fetches the exposes at that page"""
        return self.get_soup_from_url(search_url)

    def get_soup_from_url(self, url, driver=None, captcha_api_key=None, checkbox=None, afterlogin_string=None):
        """Creates a Soup object from the HTML at the provided URL"""

        self.rotate_user_agent()
        resp = requests.get(url, headers=self.HEADERS)
        if resp.status_code != 200:
            self.__log__.error("Got response (%i): %s", resp.status_code, resp.content)
        if self.config.use_proxy():
            return self.get_soup_with_proxy(url)
        if driver is not None:
            driver.get(url)
            if re.search("geetest", driver.page_source):
                self.resolvegeetest(driver, checkbox, afterlogin_string, captcha_api_key)
            elif re.search("g-recaptcha", driver.page_source):
                self.resolvecaptcha(driver, checkbox, afterlogin_string, captcha_api_key)

            return BeautifulSoup(driver.page_source, 'html.parser')
        return BeautifulSoup(resp.content, 'html.parser')

    def get_soup_with_proxy(self, url):
        """Will try proxies until it's possible to crawl and return a soup"""
        resolved = False
        resp = None

        # We will keep trying to fetch new proxies until one works
        while not resolved:
            proxies_list = proxies.get_proxies()
            for proxy in proxies_list:
                self.rotate_user_agent()

                try:
                    # Very low proxy read timeout, or it will get stuck on slow proxies
                    resp = requests.get(url, headers=self.HEADERS, proxies={"http": proxy, "https": proxy},
                                        timeout=(20, 0.1))

                    if resp.status_code != 200:
                        self.__log__.error("Got response (%i): %s", resp.status_code, resp.content)
                    else:
                        resolved = True
                        break

                except requests.exceptions.ConnectionError:
                    self.__log__.error("Connection failed for proxy %s. Trying new proxy...", proxy)
                except requests.exceptions.Timeout:
                    self.__log__.error("Connection timed out for proxy %s. Trying new proxy...", proxy)
                except:
                    self.__log__.error("Some error occurred. Trying new proxy...")

        if not resp:
            raise Exception("An error occurred while fetching proxies or content")

        return BeautifulSoup(resp.content, 'html.parser')

    # pylint: disable=no-self-use
    def extract_data(self, soup):
        """Should be implemented in subclass"""
        raise Exception("Method not implemented")

    # pylint: disable=unused-argument
    def get_results(self, search_url, max_pages=None):
        """Loads the exposes from the site, starting at the provided URL"""
        self.__log__.debug("Got search URL %s", search_url)

        # load first page
        soup = self.get_page(search_url)

        # get data from first page
        entries = self.extract_data(soup)
        self.__log__.debug('Number of found entries: %d', len(entries))

        return entries

    def crawl(self, url, max_pages=None):
        """Load as many exposes as possible from the provided URL"""
        if re.search(self.URL_PATTERN, url):
            try:
                return self.get_results(url, max_pages)
            except requests.exceptions.ConnectionError:
                self.__log__.warning("Connection to %s failed. Retrying.", url.split('/')[2])
                return []
        return []

    def get_name(self):
        """Returns the name of this crawler"""
        return type(self).__name__

    def get_expose_details(self, expose):
        """Loads additional detalis for an expose. Should be implemented in the subclass"""
        return expose

    def resolvecaptcha(self, driver, checkbox: bool, afterlogin_string: str = "", api_key: str = None):
        iframe_present = self._check_if_iframe_visible(driver)
        if checkbox is False and afterlogin_string == "" and iframe_present:
            self._solve(driver, api_key)
        else:
            if checkbox:
                self._clickcaptcha(driver, checkbox)
            else:
                self._wait_for_captcha_resolution(driver, checkbox, afterlogin_string)

    def resolvegeetest(self, driver, checkbox: bool, afterlogin_string: str = "", api_key: str = None):
        # driver.execute_script('chrome.webRequest.onBeforeRequest.addListener(function() {return {cancel: true};},{urls: ["*://api.geetest.com/*"]},["blocking"]);')
        geetest_present = self._check_if_geetest_visible(driver)
        gt = driver.execute_script('return window.GeeGT')
        challenge = driver.execute_script('return window.GeeChallenge')
        # print('geetest')
        # print(gt)
        # print(challenge)
        m = re.search("data: \"(.+)\"", driver.page_source)
        if gt and challenge and m and m.group(1):
          url = driver.current_url
          session = requests.Session()
          postrequest = (
              f"http://2captcha.com/in.php?key={api_key}&method=geetest&gt={gt}&api-server=api.geetest.com&challenge={challenge}&pageurl={urllib.parse.quote_plus(url)}"
          )
          captcha_id = session.post(postrequest).text.split("|")[1]
          geetest_answer = session.get(f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}").text
          while "CAPCHA_NOT_READY" in geetest_answer:
              sleep(5)
              self.__log__.debug("Captcha status: %s", geetest_answer)
              geetest_answer = session.get(f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}").text
          self.__log__.debug("Captcha promise: %s", geetest_answer)
          geetest_answer = geetest_answer[geetest_answer.find('|')+1:]
          geetest_answer = json.loads(geetest_answer)
          answer_challenge = geetest_answer['geetest_challenge']
          answer_seccode = geetest_answer['geetest_seccode']
          answer_validate = geetest_answer['geetest_validate']
          driver.execute_script(f'solvedCaptcha({{ geetest_challenge: "{answer_challenge}", geetest_seccode: "{answer_seccode}", geetest_validate: "{answer_validate}", data: "{m.group(1)}"}})')
          self._check_if_geetest_not_visible(driver)

    def _solve(self, driver, api_key):
        google_site_key = driver.find_element_by_class_name("g-recaptcha").get_attribute("data-sitekey")
        self.__log__.debug("Google site key: %s", google_site_key)
        url = driver.current_url
        session = requests.Session()
        postrequest = (
            f"http://2captcha.com/in.php?key={api_key}&method=userrecaptcha&googlekey={google_site_key}&pageurl={url}"
        )
        captcha_id = session.post(postrequest).text.split("|")[1]
        recaptcha_answer = session.get(f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}").text
        while "CAPCHA_NOT_READY" in recaptcha_answer:
            sleep(5)
            self.__log__.debug("Captcha status: %s", recaptcha_answer)
            recaptcha_answer = session.get(f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}").text
        self.__log__.debug("Captcha promise: %s", recaptcha_answer)
        recaptcha_answer = recaptcha_answer.split("|")[1]
        driver.execute_script(f'document.getElementById("g-recaptcha-response").innerHTML="{recaptcha_answer}";')
        # TODO: Below function call can be different depending on the websites implementation. It is responsible for
        #  sending the the promise that we get from recaptcha_answer. For now, if it breaks, it is required to
        #  reverse engineer it by hand. Not sure if there is a way to automate it.
        driver.execute_script(f'solvedCaptcha("{recaptcha_answer}")')
        self._check_if_iframe_not_visible(driver)

    def _clickcaptcha(self, driver, checkbox: bool):
        driver.switch_to.frame(driver.find_element_by_tag_name("iframe"))
        recaptcha_checkbox = driver.find_element_by_class_name("recaptcha-checkbox-checkmark")
        recaptcha_checkbox.click()
        self._wait_for_captcha_resolution(driver, checkbox)
        driver.switch_to.default_content()

    def _wait_for_captcha_resolution(self, driver, checkbox: bool, afterlogin_string=""):
        if checkbox:
            try:
                element = WebDriverWait(driver, 120).until(
                    EC.visibility_of_element_located((By.CLASS_NAME, "recaptcha-checkbox-checked"))
                )
            except selenium.common.exceptions.TimeoutException:
                print("Selenium.Timeoutexception")
        else:
            xpath_string = f"//*[contains(text(), '{afterlogin_string}')]"
            try:
                element = WebDriverWait(driver, 120).until(EC.visibility_of_element_located((By.XPATH, xpath_string)))
            except selenium.common.exceptions.TimeoutException:
                print("Selenium.Timeoutexception")

    def _check_if_iframe_visible(self, driver: selenium.webdriver.Chrome):
        try:
            iframe = WebDriverWait(driver, 10).until(EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "iframe[src^='https://www.google.com/recaptcha/api2/anchor?']")))
            return iframe
        except NoSuchElementException:
            print("No iframe found, therefore no chaptcha verification necessary")
        # except selenium.common.exceptions.TimeoutException:
        #     print("Timeout on recaptcha")

    def _check_if_geetest_visible(self, driver: selenium.webdriver.Chrome):
        try:
            geetest = WebDriverWait(driver, 10).until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div#captcha-box")))
            return geetest
        except NoSuchElementException:
            print("No geetest found, therefore no chaptcha verification necessary")

    def _check_if_geetest_not_visible(self, driver: selenium.webdriver.Chrome):
        try:
            geetest = WebDriverWait(driver, 10).until(EC.invisibility_of_element(
                (By.CSS_SELECTOR, "div.main__captcha")))
            return geetest
        except NoSuchElementException:
            print("Element not found")

    def _check_if_iframe_not_visible(self, driver: selenium.webdriver.Chrome):
        try:
            iframe = WebDriverWait(driver, 10).until(EC.invisibility_of_element(
                (By.CSS_SELECTOR, "iframe[src^='https://www.google.com/recaptcha/api2/anchor?']")))
            return iframe
        except NoSuchElementException:
            print("Element not found")
