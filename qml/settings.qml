import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI
import ClassWidgets.Plugins

// ═══════════════════════════════════════════════════════════════════
// TTS 自动播报 · 设置页
//
// 后端：main.py（槽函数名/参数个数/返回类型必须与下文 backend.xxx() 一致）
// 规则来源：qml-settings-guide.md / cw-plugin-dev-guide.md
//
//   ① 所有 root.xxx 都是显式声明的 property
//      —— Qt 6 给未声明属性赋值静默失败、读取恒为 undefined，qmllint 不报
//   ② 所有会写后端的事件处理器，第一行 if (!root.ready) return
//      —— 否则 Component.onCompleted 赋初值就会把设置写一遍
//   ③ 结构性数据（模板列表）换新数组/新对象，字段编辑原地改
//   ④ TextField 不在输入过程中写盘，一律失焦或回车写回
//      —— 边打字边写盘会重建 delegate，输入框失焦、光标跳位
//   ⑤ 后端改动只走 Signal + Connections，不轮询
//   ⑥ Switch 必须覆盖 checkedText/uncheckedText（RinUI 默认渲染 On/Off）
//
// ⚠️ 状态属性不叫 enabled：那是 Item.enabled，被覆盖后关掉总开关
//    会把整页控件连带禁用，用户再也点不回来。
// ═══════════════════════════════════════════════════════════════════

PluginPage {
    id: root
    pluginId: "com.su.tts"
    title: qsTr("TTS 自动播报")

    // ═══════════════════ 状态属性 ═══════════════════
    property bool ready: false            // 规则②：初始化守卫
    property bool syncing: false          // 回填数据期间禁止写回后端
    property string resultText: ""        // 统一结果提示（规则：一出口）
    property bool resultOk: true

    property var engineModel: []          // [{name:"auto",available:"true"}, ...]
    property var voiceModel: []           // [{id,name,locale}, ...]
    property var templateKeys: []         // 结构性数据：换新数组
    property var templateTexts: ({})      // key → 当前文案（换新对象）

    property string engineId: "auto"      // 配置里选的引擎
    property string activeEngine: "none"  // 实际在跑的引擎
    property string voiceId: ""
    property string localeId: ""          // 语音语言筛选（空 = 全部）
    property real volumeValue: 1.0
    property real rateValue: 1.0
    property bool pluginEnabled: true
    property string testText: ""

    // Piper 离线模型三态：unavailable / missing / ready
    property string piperState: "unavailable"
    property bool piperDownloading: false
    property int piperPercent: 0
    property string piperFileName: ""

    // 「快速插入变量」的目标：最近获得焦点的模板输入框
    property string editKey: ""
    property var editField: null

    // ═══════════════════ 普通函数 ═══════════════════
    // 规则③：派生 property 依赖的函数必须声明在派生属性之前。

    function setResult(msg, ok) {
        root.resultText = msg
        root.resultOk = ok !== false
    }

    // 对照表替代 if-else 链（规则：图标/文案映射）
    function engineLabel(id) {
        var labels = {
            "auto": qsTr("自动（推荐）"),
            "piper": qsTr("Piper 本地离线"),
            "edge": qsTr("EdgeTTS 在线"),
            "winrt": qsTr("Windows 系统语音"),
            "pyttsx3": qsTr("pyttsx3 兜底")
        }
        return labels[id] || id
    }

    function activityLabel(key) {
        var labels = {
            "class": qsTr("上课"),
            "break": qsTr("课间休息"),
            "activity": qsTr("活动"),
            "free": qsTr("放学"),
            "preparation": qsTr("预备铃")
        }
        return labels[key] || key
    }

    function activityIcon(key) {
        var icons = {
            "class": "ic_fluent_book_20_regular",
            "break": "ic_fluent_drink_coffee_20_regular",
            "activity": "ic_fluent_people_20_regular",
            "free": "ic_fluent_home_20_regular",
            "preparation": "ic_fluent_alert_20_regular"
        }
        return icons[key] || "ic_fluent_text_description_20_regular"
    }

    // ── 下标换算：越界一律回落到安全值，调用方不必自己防 ──
    function indexOfEngine(id) {
        for (var i = 0; i < root.engineModel.length; i++) {
            if (root.engineModel[i].name === id) return i
        }
        return 0
    }

    function localesOf(list) {
        var seen = {}
        var out = []
        for (var i = 0; i < list.length; i++) {
            var loc = list[i].locale || ""
            if (loc && !seen[loc]) {
                seen[loc] = true
                out.push(loc)
            }
        }
        out.sort()
        return out
    }

    // 规则③：无筛选时也返回新数组，不把 voiceModel 的引用直接交出去
    function voicesFor(locale) {
        if (!locale) return root.voiceModel.slice()
        var out = []
        for (var i = 0; i < root.voiceModel.length; i++) {
            if (root.voiceModel[i].locale === locale) out.push(root.voiceModel[i])
        }
        return out
    }

    function localeOfVoice(id) {
        for (var i = 0; i < root.voiceModel.length; i++) {
            if (root.voiceModel[i].id === id) return root.voiceModel[i].locale || ""
        }
        return ""
    }

    function voiceLabel(id) {
        for (var i = 0; i < root.voiceModel.length; i++) {
            if (root.voiceModel[i].id === id) return root.voiceModel[i].name
        }
        return id
    }

    // ── 数据拉取：每个信号只刷自己那一份，避免整页重算 ──
    // syncing：回填期间设真，事件处理器据此跳过 —— 否则「刷新数据」
    // 会被当成「用户改了选项」，把旧值又写回后端。
    function refreshEngines() {
        if (!backend) return
        root.syncing = true
        root.engineModel = backend.getAvailableEngines()
        root.engineId = backend.getCurrentEngine()
        root.activeEngine = backend.getActiveEngineName()
        root.syncing = false
    }

    function refreshVoices() {
        if (!backend) return
        root.syncing = true
        root.voiceModel = backend.getVoiceList()
        root.voiceId = backend.getCurrentVoice()
        root.localeId = root.localeOfVoice(root.voiceId)
        root.syncing = false
    }

    function refreshConfig() {
        if (!backend) return
        root.syncing = true
        root.pluginEnabled = backend.isEnabled()
        root.volumeValue = backend.getVolume()
        root.rateValue = backend.getRate()
        root.engineId = backend.getCurrentEngine()
        root.voiceId = backend.getCurrentVoice()
        root.activeEngine = backend.getActiveEngineName()
        root.syncing = false
    }

    function refreshTemplates() {
        if (!backend) return
        var keys = backend.getTemplateKeys()
        var texts = {}
        for (var i = 0; i < keys.length; i++) texts[keys[i]] = backend.getTemplate(keys[i])
        root.templateKeys = keys        // 新数组 → Repeater 重建
        root.templateTexts = texts      // 新对象 → 绑定刷新
    }

    function refreshPiper() {
        if (!backend) return
        root.piperState = backend.getPiperModelState()
    }

    function refreshAll() {
        root.refreshEngines()
        root.refreshVoices()
        root.refreshConfig()
        root.refreshTemplates()
        root.refreshPiper()
    }

    // ── 写操作：入参当脏数据，改完发信号由后端回传真实值 ──
    function applyEngine(index) {
        if (!root.ready || root.syncing) return
        if (index < 0 || index >= root.engineModel.length) return
        var name = root.engineModel[index].name
        if (name === root.engineId) return
        backend.setEngine(name)
        backend.refreshVoiceList()
        root.setResult(qsTr("已切换到 %1").arg(root.engineLabel(name)), true)
    }

    function applyLocale(index) {
        if (!root.ready || root.syncing) return
        // 只改内存里的筛选条件，不写后端
        root.localeId = index <= 0 ? "" : (root.localeList[index] || "")
    }

    function applyVoice(index) {
        if (!root.ready || root.syncing) return
        var list = root.filteredVoices
        var id = ""
        var shown = qsTr("引擎默认语音")
        if (index > 0 && index <= list.length) {
            id = list[index - 1].id
            shown = list[index - 1].name
        } else if (index === 0 && root.localeId && list.length > 0) {
            // 选定语言后选「默认」= 该语言的第一条
            id = list[0].id
            shown = list[0].name
        }
        if (id === root.voiceId) return
        backend.setVoice(id)
        root.setResult(qsTr("已选择语音 %1").arg(shown), true)
    }

    // 规则④：只在失焦 / 回车时调用
    function commitTemplate(key, field) {
        if (!root.ready || !field) return
        var text = field.text
        if (text === (root.templateTexts[key] || "")) return
        backend.setTemplate(key, text)
        var next = {}
        for (var k in root.templateTexts) next[k] = root.templateTexts[k]
        next[key] = text
        root.templateTexts = next
        root.setResult(qsTr("已保存「%1」的朗读文案").arg(root.activityLabel(key)), true)
    }

    function resetTemplate(key, field) {
        if (!root.ready) return
        backend.resetTemplate(key)
        var text = backend.getTemplate(key)
        var next = {}
        for (var k in root.templateTexts) next[k] = root.templateTexts[k]
        next[key] = text
        root.templateTexts = next
        if (field) field.text = text
        root.setResult(qsTr("「%1」已恢复默认文案").arg(root.activityLabel(key)), true)
    }

    function insertVariable(token) {
        var field = root.editField
        if (!field) {
            root.setResult(qsTr("请先点一下要编辑的朗读文案输入框"), false)
            return
        }
        var t = field.text
        var pos = field.cursorPosition
        field.text = t.substring(0, pos) + token + t.substring(pos)
        field.cursorPosition = pos + token.length     // 不写这句光标会跳到末尾
        field.forceActiveFocus()
        root.commitTemplate(root.editKey, field)
    }

    function speakTemplate(key) {
        if (!backend) return
        var text = root.templateTexts[key] || ""
        if (!text) {
            root.setResult(qsTr("这条文案是空的，没有可朗读的内容"), false)
            return
        }
        // 试听必须把变量填满，否则用户听到的是残缺句子，会以为模板写坏了
        text = text.replace(/\{subject\}/g, qsTr("语文"))
                   .replace(/\{teacher\}/g, qsTr("张老师"))
                   .replace(/\{location\}/g, qsTr("3号楼201"))
                   .replace(/\{next_subject\}/g, qsTr("数学"))
                   .replace(/\{next_teacher\}/g, qsTr("李老师"))
                   .replace(/\{next_location\}/g, qsTr("本班教室"))
                   .replace(/\{title\}/g, qsTr("通知标题"))
                   .replace(/\{message\}/g, qsTr("通知内容"))
        backend.testSpeak(text)
        root.setResult(qsTr("正在试听「%1」").arg(root.activityLabel(key)), true)
    }

    function speakTest() {
        if (!backend) return
        var text = root.testText.trim()
        if (!text) text = qsTr("同学们好，现在开始上课。")
        backend.testSpeak(text)
        root.setResult(qsTr("正在朗读测试文本"), true)
    }

    function startDownload() {
        if (!backend) return
        root.piperDownloading = true
        root.piperPercent = 0
        root.piperFileName = ""
        backend.downloadPiperModel()
        root.setResult(qsTr("已开始下载 Piper 模型，可继续使用其他设置"), true)
    }

    function openModelDir() {
        if (!backend) return
        var path = backend.getPiperModelPath()
        if (path) Qt.openUrlExternally("file:///" + path.replace(/\\/g, "/"))
        else root.setResult(qsTr("没有拿到模型目录路径"), false)
    }

    // ═══════════════════ 派生属性 ═══════════════════
    // 规则③：声明在上方所有函数之后；一次计算、多处引用。

    property var localeList: {
        var list = root.localesOf(root.voiceModel)
        list.unshift(qsTr("全部语言"))
        return list
    }

    property var filteredVoices: root.voicesFor(root.localeId)

    property var voiceLabels: {
        var list = root.filteredVoices
        var out = [qsTr("默认")]
        for (var i = 0; i < list.length; i++) out.push(list[i].name)
        return out
    }

    // 「未安装」和「不可用」必须区分：前者用户能自己装，后者是环境问题
    property var engineLabels: {
        var out = []
        for (var i = 0; i < root.engineModel.length; i++) {
            var e = root.engineModel[i]
            var suffix = e.available === "true"
                ? ""
                : (e.name === "pyttsx3" ? qsTr(" (未安装)") : qsTr(" (不可用)"))
            out.push(root.engineLabel(e.name) + suffix)
        }
        return out
    }

    property string engineDescription: {
        if (root.activeEngine === "none") return qsTr("当前没有可用引擎，不会朗读")
        return qsTr("实际使用 %1").arg(root.engineLabel(root.activeEngine))
    }

    property string rateDescription: {
        var pct = Math.round(root.rateValue * 100)
        if (Math.abs(root.rateValue - 1.0) < 0.01) return qsTr("正常（%1%）").arg(pct)
        return root.rateValue > 1.0
            ? qsTr("偏快（%1%）").arg(pct)
            : qsTr("偏慢（%1%）").arg(pct)
    }

    property string piperDescription: {
        if (root.piperState === "ready") return qsTr("已就绪，断网也能朗读")
        if (root.piperDownloading)
            return qsTr("下载中 %1% · %2").arg(root.piperPercent).arg(root.piperFileName)
        return qsTr("未下载（首次约 100MB，之后完全离线）")
    }

    // ═══════════════════ 生命周期 ═══════════════════
    Component.onCompleted: {
        if (!backend) return
        root.refreshAll()
        root.ready = true                 // 规则②：全部赋值完成后再置位
    }

    // ═══════════════════ 后端信号 ═══════════════════
    // 规则⑤：改完由后端发信号，UI 订阅即刷新；不用 Timer 轮询
    Connections {
        target: backend
        function onEngineChanged() {
            root.refreshEngines()
            root.refreshPiper()
        }
        function onVoiceListChanged() {
            root.refreshVoices()
        }
        function onConfigChanged() {
            root.refreshConfig()
        }
        function onModelProgressChanged(fileName, percent) {
            if (percent >= 100) {
                root.piperDownloading = false
                root.piperPercent = 100
                root.piperFileName = ""
                root.refreshPiper()
                root.refreshVoices()
                return
            }
            root.piperDownloading = true
            root.piperPercent = percent
            root.piperFileName = fileName
        }
    }

    // ═══════════════════════════════════════════
    // 总开关
    // ═══════════════════════════════════════════
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4

        SettingCard {
            Layout.fillWidth: true
            icon.name: root.pluginEnabled
                ? "ic_fluent_speaker_2_20_regular"
                : "ic_fluent_speaker_mute_20_regular"
            title: qsTr("自动播报")
            description: root.pluginEnabled
                ? root.engineDescription
                : qsTr("已关闭，收到通知时不会朗读")

            Switch {
                id: enableSwitch
                checked: root.pluginEnabled
                // 规则⑥：RinUI 的 Switch 默认渲染 On/Off
                checkedText: qsTr("开")
                uncheckedText: qsTr("关")
                onToggled: {
                    if (!root.ready || root.syncing) return
                    root.pluginEnabled = checked
                    backend.setEnabled(checked)
                    root.setResult(checked ? qsTr("已开启自动播报") : qsTr("已关闭自动播报"), true)
                }
            }
        }
    }

    // ═══════════════════════════════════════════
    // 引擎与语音
    // ═══════════════════════════════════════════
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4

        Text {
            typography: Typography.BodyStrong
            text: qsTr("引擎与语音")
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_bot_20_regular"
            title: qsTr("TTS 引擎")
            description: root.engineDescription

            ComboBox {
                id: engineCombo
                width: 220
                model: root.engineLabels
                currentIndex: root.indexOfEngine(root.engineId)
                onCurrentIndexChanged: root.applyEngine(currentIndex)
            }
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_person_voice_20_regular"
            title: qsTr("语音")
            description: root.voiceId
                ? qsTr("当前：%1").arg(root.voiceLabel(root.voiceId))
                : qsTr("使用引擎默认语音")

            RowLayout {
                spacing: 8

                ComboBox {
                    id: localeCombo
                    width: 160
                    model: root.localeList
                    currentIndex: {
                        if (!root.localeId) return 0
                        for (var i = 1; i < root.localeList.length; i++) {
                            if (root.localeList[i] === root.localeId) return i
                        }
                        return 0
                    }
                    onCurrentIndexChanged: root.applyLocale(currentIndex)
                }

                ComboBox {
                    id: voiceCombo
                    width: 240
                    model: root.voiceLabels
                    currentIndex: {
                        if (!root.voiceId) return 0
                        for (var i = 0; i < root.filteredVoices.length; i++) {
                            if (root.filteredVoices[i].id === root.voiceId) return i + 1
                        }
                        return 0
                    }
                    onCurrentIndexChanged: root.applyVoice(currentIndex)
                }

                ToolButton {
                    icon.name: "ic_fluent_arrow_sync_20_regular"
                    flat: true
                    ToolTip { text: qsTr("刷新语音列表"); visible: parent.hovered }
                    onClicked: {
                        if (!backend) return
                        backend.refreshVoiceList()
                        root.setResult(qsTr("正在获取语音列表…"), true)
                    }
                }
            }
        }
    }

    // ═══════════════════════════════════════════
    // 离线模型
    // ═══════════════════════════════════════════
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4
        visible: root.piperState !== "unavailable"

        Text {
            typography: Typography.BodyStrong
            text: qsTr("离线朗读")
        }

        SettingExpander {
            Layout.fillWidth: true
            icon.name: "ic_fluent_cloud_off_20_regular"
            title: qsTr("Piper 本地语音模型")
            description: root.piperDescription

            action: Button {
                text: root.piperState === "ready" ? qsTr("重新下载") : qsTr("下载")
                enabled: !root.piperDownloading
                icon.name: "ic_fluent_arrow_download_20_regular"
                onClicked: root.startDownload()
            }

            // SettingItem 的默认属性就是右侧内容区，控件直接放进去即可
            SettingItem {
                title: qsTr("下载进度")
                description: root.piperDownloading
                    ? qsTr("正在下载 %1").arg(root.piperFileName)
                    : qsTr("仅在需要离线朗读时下载")

                ProgressBar {
                    Layout.preferredWidth: 200
                    visible: root.piperDownloading
                    from: 0
                    to: 100
                    value: root.piperPercent
                }
            }

            SettingItem {
                title: qsTr("模型目录")
                description: backend ? backend.getPiperModelPath() : ""

                Button {
                    text: qsTr("打开目录")
                    icon.name: "ic_fluent_folder_open_20_regular"
                    onClicked: root.openModelDir()
                }
            }
        }
    }

    // ═══════════════════════════════════════════
    // 播放设置
    // ═══════════════════════════════════════════
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4

        Text {
            typography: Typography.BodyStrong
            text: qsTr("播放设置")
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_speaker_2_20_regular"
            title: qsTr("音量")
            description: qsTr("%1%").arg(Math.round(root.volumeValue * 100))

            RowLayout {
                spacing: 8

                Slider {
                    id: volumeSlider
                    width: 180
                    from: 0
                    to: 1
                    stepSize: 0.05
                    value: root.volumeValue
                    onMoved: {
                        if (!root.ready) return
                        backend.setVolume(value)
                    }
                }

                Text {
                    width: 44
                    typography: Typography.Caption
                    color: Colors.proxy.textSecondaryColor
                    text: qsTr("%1%").arg(Math.round(root.volumeValue * 100))
                }
            }
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_rocket_20_regular"
            title: qsTr("语速")
            description: root.rateDescription

            RowLayout {
                spacing: 8

                Slider {
                    id: rateSlider
                    width: 180
                    from: 0.5
                    to: 2.0
                    stepSize: 0.1
                    value: root.rateValue
                    onMoved: {
                        if (!root.ready) return
                        backend.setRate(value)
                    }
                }

                Text {
                    width: 44
                    typography: Typography.Caption
                    color: Colors.proxy.textSecondaryColor
                    text: qsTr("%1%").arg(Math.round(root.rateValue * 100))
                }
            }
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_play_20_regular"
            title: qsTr("测试朗读")
            description: qsTr("用当前引擎、语音、音量、语速读一句")

            Button {
                text: qsTr("测试")
                icon.name: "ic_fluent_play_20_regular"
                onClicked: root.speakTest()
            }
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_text_font_20_regular"
            title: qsTr("自定义测试文本")
            description: qsTr("留空则使用默认测试语句")

            TextField {
                id: testField
                Layout.preferredWidth: 260
                placeholderText: qsTr("同学们好，现在开始上课。")
                // 规则④：只在失焦/回车写本地状态，不写磁盘
                text: root.testText
                onTextChanged: {
                    if (activeFocus) root.testText = text
                }
                onEditingFinished: root.testText = text
            }
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_info_20_regular"
            title: qsTr("语速适用范围")
            description: qsTr("仅 Piper 本地引擎支持调速；在线与系统语音保持原速。")
        }
    }

    // ═══════════════════════════════════════════
    // 朗读文案
    // ═══════════════════════════════════════════
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4

        Text {
            typography: Typography.BodyStrong
            text: qsTr("朗读文案")
        }

        Text {
            Layout.fillWidth: true
            typography: Typography.Caption
            color: Colors.proxy.textSecondaryColor
            wrapMode: Text.WordWrap
            text: qsTr("可用变量：{subject} 科目 {teacher} 教师 {location} 地点 "
                     + "{next_subject} 下节科目 {next_teacher} 下节教师 {next_location} 下节地点 "
                     + "{title} 通知标题 {message} 通知正文")
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            Text {
                typography: Typography.Caption
                color: Colors.proxy.textSecondaryColor
                text: qsTr("快速插入：")
            }

            Repeater {
                model: ["{subject}", "{teacher}", "{location}", "{next_subject}",
                        "{next_teacher}", "{next_location}", "{title}", "{message}"]

                Button {
                    required property string modelData
                    text: modelData
                    flat: true
                    implicitHeight: 24
                    enabled: root.editField !== null
                    onClicked: root.insertVariable(modelData)
                }
            }
        }

        // 规则③：模板列表是结构性数据，model 只认 root.templateKeys 这个数组
        Repeater {
            model: root.templateKeys

            SettingExpander {
                id: templateExpander
                required property int index
                required property string modelData

                Layout.fillWidth: true
                icon.name: root.activityIcon(templateExpander.modelData)
                title: root.activityLabel(templateExpander.modelData)
                description: root.templateTexts[templateExpander.modelData] || ""

                SettingItem {
                    title: qsTr("朗读模板")

                    RowLayout {
                        spacing: 8

                        TextField {
                            id: templateField
                            Layout.preferredWidth: 300
                            // 规则④：显示值来自 QML 唯一副本，失焦/回车才写后端
                            text: root.templateTexts[templateExpander.modelData] || ""
                            placeholderText: backend
                                ? backend.getDefaultTemplate(templateExpander.modelData) : ""

                            onActiveFocusChanged: {
                                if (activeFocus) {
                                    root.editKey = templateExpander.modelData
                                    root.editField = templateField
                                } else {
                                    root.commitTemplate(templateExpander.modelData, templateField)
                                }
                            }
                            onEditingFinished: {
                                root.commitTemplate(templateExpander.modelData, templateField)
                            }
                        }

                        ToolButton {
                            icon.name: "ic_fluent_arrow_reset_20_regular"
                            flat: true
                            ToolTip { text: qsTr("恢复默认"); visible: parent.hovered }
                            onClicked: root.resetTemplate(
                                templateExpander.modelData, templateField)
                        }

                        Button {
                            text: qsTr("试听")
                            icon.name: "ic_fluent_play_20_regular"
                            onClicked: root.speakTemplate(templateExpander.modelData)
                        }
                    }
                }
            }
        }
    }

    // ═══════════════════════════════════════════
    // 结果提示（全页唯一出口）
    // ═══════════════════════════════════════════
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4
        visible: root.resultText !== ""

        Text {
            Layout.fillWidth: true
            typography: Typography.Caption
            wrapMode: Text.WordWrap
            text: root.resultText
            color: root.resultOk
                ? (Theme.currentTheme.isDark ? "#81C784" : "#2E7D32")
                : (Theme.currentTheme.isDark ? "#EF9A9A" : "#C62828")
        }
    }
}
