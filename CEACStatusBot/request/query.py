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
                # 启动 Chromium 浏览器，设置真实 User-Agent
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800}
                )
                page = context.new_page()

                # 1. 打开 CEAC 页面并等待加载完成（自动过 Cloudflare JS 验证）
                url = f"{ROOT}/ceacstattracker/status.aspx?App=NIV"
                page.goto(url, wait_until="networkidle", timeout=60000)

                # 检查验证码图片元素是否存在
                captcha_img_selector = "#c_status_ctl00_contentplaceholder1_defaultcaptcha_CaptchaImage"
                try:
                    page.wait_for_selector(captcha_img_selector, timeout=15000)
                except Exception:
                    print("未能找到验证码元素，可能仍被 Cloudflare 拦截或页面超时。")
                    browser.close()
                    continue

                # 2. 截图获取验证码图片（直接截取验证码元素的 bytes）
                captcha_element = page.query_selector(captcha_img_selector)
                captcha_bytes = captcha_element.screenshot()

                # 3. 识别验证码
                captcha_num = captchaHandle.solve(captcha_bytes)
                print(f"Captcha solved: {captcha_num}")

                # 4. 选择 Location 下拉框
                location_select = page.query_selector("#ctl00_ContentPlaceHolder1_Location_Dropdown")
                if not location_select:
                    print("未找到 Location 下拉框")
                    browser.close()
                    continue

                # 在下拉选项中匹配 location 文本并选中 value
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

                # 5. 填写表单其他字段
                page.fill("#ctl00_ContentPlaceHolder1_Visa_Case_Number", application_num)
                page.fill("#ctl00_ContentPlaceHolder1_Passport_Number", passport_number)
                page.fill("#ctl00_ContentPlaceHolder1_Surname", surname)
                page.fill("#ctl00_ContentPlaceHolder1_Captcha", captcha_num)

                # 6. 点击提交按钮并等待网络请求响应/页面更新
                with page.expect_navigation(wait_until="networkidle", timeout=30000):
                    page.click("#ctl00_ContentPlaceHolder1_btnSubmit")

                # 7. 解析返回的结果页面
                content = page.content()
                browser.close()

                soup = BeautifulSoup(content, features="lxml")
                status_tag = soup.find("span", id="ctl00_ContentPlaceHolder1_ucApplicationStatusView_lblStatus")
                
                # 如果没找到 status，可能是验证码填错或其他错误，进入下一次重试
                if not status_tag:
                    print("未能获取状态，可能验证码识别错误，尝试重试...")
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
