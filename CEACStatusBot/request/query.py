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
                # 关键：开启浏览器防检测配置
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ]
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                    timezone_id="America/New_York",
                )

                page = context.new_page()

                # 注入 JavaScript 覆盖 navigator.webdriver 标志，防止被 Cloudflare 识别为 Bot
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)

                # 1. 打开页面：使用 domcontentloaded 代替 networkidle 避免超时
                url = f"{ROOT}/ceacstattracker/status.aspx?App=NIV"
                print("Navigating to CEAC page...")
                page.goto(url, wait_until="domcontentloaded", timeout=45000)

                # 保存当前页面截图
                page.screenshot(path="debug_page.png", full_page=True)
                # 保存当前页面 HTML 源码
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(page.content())

                # 2. 给予 Cloudflare 自检页面（Turnstile Challenge）通过的时间
                captcha_img_selector = "#c_status_ctl00_contentplaceholder1_defaultcaptcha_CaptchaImage"
                
                # 等待验证码图片出现，最多等待 30 秒
                try:
                    page.wait_for_selector(captcha_img_selector, state="visible", timeout=30000)
                except Exception:
                    print("未能找到验证码元素，可能仍被 Cloudflare 拦截或页面加载缓慢。")
                    # 保存截图供调试（可选）
                    # page.screenshot(path="cf_blocked.png")
                    browser.close()
                    continue

                # 3. 截取验证码图片
                captcha_element = page.query_selector(captcha_img_selector)
                captcha_bytes = captcha_element.screenshot()

                # 4. 识别验证码
                captcha_num = captchaHandle.solve(captcha_bytes)
                print(f"Captcha solved: {captcha_num}")

                # 5. 获取并选择 Location
                location_select = page.query_selector("#ctl00_ContentPlaceHolder1_Location_Dropdown")
                if not location_select:
                    print("未找到 Location 下拉框")
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

                # 6. 填写表单
                page.fill("#ctl00_ContentPlaceHolder1_Visa_Case_Number", application_num)
                page.fill("#ctl00_ContentPlaceHolder1_Passport_Number", passport_number)
                page.fill("#ctl00_ContentPlaceHolder1_Surname", surname)
                page.fill("#ctl00_ContentPlaceHolder1_Captcha", captcha_num)

                # 7. 提交表单并等待响应
                page.click("#ctl00_ContentPlaceHolder1_btnSubmit")
                
                # 等待状态显示区域或错误提示出现
                try:
                    page.wait_for_selector("#ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblStatus", timeout=20000)
                except Exception:
                    print("提交后未能看到状态结果，可能验证码错误或提交失败。")

                # 8. 解析结果
                content = page.content()
                browser.close()

                soup = BeautifulSoup(content, features="lxml")
                status_tag = soup.find("span", id="ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblStatus")
                
                if not status_tag:
                    print("未能获取状态，准备重试...")
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
            print(f"Playwright execution error: {e}")
            continue

    return result
