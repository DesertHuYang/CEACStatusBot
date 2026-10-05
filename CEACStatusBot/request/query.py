import time
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from CEACStatusBot.captcha import CaptchaHandle, OnnxCaptchaHandle

def query_status(location, application_num, passport_number, surname, captchaHandle: CaptchaHandle = OnnxCaptchaHandle("captcha.onnx")):
    failCount = 0
    result = {
        "success": False,
    }
    backupTime = 5
    ROOT = "https://ceac.state.gov"

    while failCount < 5:
        if failCount > 0:
            print(f"Retrying... Attempt {failCount + 1} / 5 in {backupTime} seconds")
            time.sleep(backupTime)
        failCount += 1

        try:
            with sync_playwright() as p:
                # 启动 Chromium 并关闭自动化标志与 Chrome 特征标记
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-infobars",
                        "--window-size=1920,1080",
                    ]
                )
                
                # 配置真实的 Windows Chrome 上下文
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    timezone_id="America/New_York",
                    device_scale_factor=1,
                    has_touch=False,
                    is_mobile=False,
                )

                page = context.new_page()

                # 注入抹除常见 Headless 痕迹的 JavaScript
                page.add_init_script("""
                    // 隐藏 navigator.webdriver
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    
                    # 伪装 languages
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en']
                    });
                    
                    # 伪装 plugins 长度
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    
                    # 伪装 chrome 对象
                    window.chrome = {
                        runtime: {}
                    };
                """)

                # 1. 访问目标页面
                url = f"{ROOT}/ceacstattracker/status.aspx?App=NIV"
                print("Navigating to CEAC page...")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # 2. 等待 Cloudflare 5秒盾跳转并渲染出验证码图片
                captcha_img_selector = "#c_status_ctl00_contentplaceholder1_defaultcaptcha_CaptchaImage"
                try:
                    # 给 Cloudflare 充分的跳转与验证时间，最长等待 35 秒
                    page.wait_for_selector(captcha_img_selector, state="visible", timeout=35000)
                except Exception:
                    print("--> [DEBUG] 未能加载验证码，当前页面标题:", page.title())
                    browser.close()
                    continue

                # 3. 截取当前页面上的验证码图片
                captcha_element = page.query_selector(captcha_img_selector)
                captcha_bytes = captcha_element.screenshot()

                # 4. 识别验证码
                captcha_num = captchaHandle.solve(captcha_bytes)
                print(f"Captcha solved: {captcha_num}")

                # 5. 获取并选择 Location 下拉框
                location_select = page.query_selector("#ctl00_ContentPlaceHolder1_Location_Dropdown")
                if not location_select:
                    print("--> [DEBUG] 未找到 Location 下拉框")
                    browser.close()
                    continue

                options = page.eval_on_selector_all(
                    "#ctl00_ContentPlaceHolder1_Location_Dropdown option",
                    "opts => opts.map(o => ({text: o.innerText, value: o.value}))"
                )
                location_value = None
                for opt in options:
                    if location in opt["text"]:
                        location_value = opt["value"]
                        break

                if not location_value:
                    print("Location not found in dropdown options.")
                    browser.close()
                    return {"success": False}

                page.select_option("#ctl00_ContentPlaceHolder1_Location_Dropdown", location_value)

                # 6. 填写表单字段
                page.fill("#ctl00_ContentPlaceHolder1_Visa_Case_Number", application_num)
                page.fill("#ctl00_ContentPlaceHolder1_Passport_Number", passport_number)
                page.fill("#ctl00_ContentPlaceHolder1_Surname", surname)
                page.fill("#ctl00_ContentPlaceHolder1_Captcha", captcha_num)

                # 7. 提交表单
                page.click("#ctl00_ContentPlaceHolder1_btnSubmit")
                
                try:
                    page.wait_for_selector("#ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblStatus", timeout=20000)
                except Exception:
                    print("--> [DEBUG] 提交后未检测到结果页面，可能验证码识别错误，准备重试...")

                # 8. 解析网页内容
                content = page.content()
                browser.close()

                soup = BeautifulSoup(content, features="lxml")
                status_tag = soup.find("span", id="ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblStatus")
                
                if not status_tag:
                    continue

                application_num_returned = soup.find("span", id="ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblCaseNo").string
                assert application_num_returned == application_num
                status = status_tag.string
                visa_type = soup.find("span", id="ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblAppName").string
                case_created = soup.find("span", id="ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblSubmitDate").string
                case_last_updated = soup.find("span", id="ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblStatusDate").string
                description = soup.find("span", id="ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblMessage").string

                result.update({
                    "success": True,
                    "time": str(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())),
                    "visa_type": visa_type,
                    "status": status,
                    "case_created": case_created,
                    "case_last_updated": case_last_updated,
                    "description": description,
                    "application_num": application_num_returned,
                    "application_num_origin": application_num
                })
                break

        except Exception as e:
            print(f"--> [DEBUG] Playwright Exception: {e}")
            continue

    return result
