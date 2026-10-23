import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI
import ClassWidgets.Plugins


PluginPage {
    id: root
    pluginId: "com.su.tts"
    title: "TTS 自动播报"

    // activity key → 显示名称
    readonly property var activityLabels: ({
        "class": qsTr("上课"),
        "break": qsTr("课间休息"),
        "activity": qsTr("活动"),
        "free": qsTr("放学"),
        "preparation": qsTr("预备铃")
    })

    property var engineModel: []
    property var voiceModel: []
    property string selectedLocale: ""
    property string currentEngine: ""
    property string currentVoice: ""
    property real currentVolume: 1.0
    property real currentRate: 1.0
    property bool speakerEnabled: true
    property string customTestText: ""
    property string activeEngine: ""
    property bool ready: false

    // Piper 离线模型状态：unavailable / missing / ready
    property string piperState: "unavailable"
    property bool piperDownloading: false
    property int piperPercent: 0
    property string piperFileName: ""

    // 「快速插入变量」作用的目标：最近展开/编辑的场景
    property string activeTemplateKey: ""
    property var activeTemplateField: null

    function refreshPiperState() {
        if (!backend) return
        piperState = backend.getPiperModelState()
    }

    function getLocales() {
        let seen = {}
        let locales = []
        for (let i = 0; i < voiceModel.length; i++) {
            let loc = voiceModel[i].locale || ""
            if (loc && !seen[loc]) {
                seen[loc] = true
                locales.push(loc)
            }
        }
        locales.sort()
        return locales
    }

    function getVoicesForLocale(locale) {
        let filtered = []
        for (let i = 0; i < voiceModel.length; i++) {
            if (voiceModel[i].locale === locale) filtered.push(voiceModel[i])
        }
        return filtered
    }

    Component.onCompleted: {
        if (!backend) return
        engineModel = backend.getAvailableEngines()
        currentEngine = backend.getCurrentEngine()
        currentVoice = backend.getCurrentVoice()
        currentVolume = backend.getVolume()
        currentRate = backend.getRate()
        speakerEnabled = backend.isEnabled()
        activeEngine = backend.getActiveEngineName()
        backend.refreshVoiceList()
        refreshPiperState()
        ready = true
    }

    Connections {
        target: backend
        function onEngineChanged() {
            currentEngine = backend.getCurrentEngine()
            activeEngine = backend.getActiveEngineName()
            refreshPiperState()
        }
        function onVoiceListChanged() {
            voiceModel = backend.getVoiceList()
            if (currentVoice) {
                for (let i = 0; i < voiceModel.length; i++) {
                    if (voiceModel[i].id === currentVoice) {
                        selectedLocale = voiceModel[i].locale || ""
                        return
                    }
                }
            }
            selectedLocale = ""
        }
        function onConfigChanged() {
            currentVoice = backend.getCurrentVoice()
            currentVolume = backend.getVolume()
            currentRate = backend.getRate()
            speakerEnabled = backend.isEnabled()
        }
        function onModelProgressChanged(fileName, percent) {
            if (percent >= 100) {
                piperDownloading = false
                piperPercent = 100
                piperFileName = ""
                refreshPiperState()
                return
            }
            piperDownloading = true
            piperPercent = percent
            piperFileName = fileName
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
            icon.name: speakerEnabled
                ? "ic_fluent_speaker_2_20_regular"
                : "ic_fluent_speaker_mute_20_regular"
            title: qsTr("自动播报")
            description: speakerEnabled
                ? (activeEngine !== "none"
                    ? qsTr("已开启 · 当前引擎 %1").arg(activeEngine)
                    : qsTr("已开启，但暂无可用引擎"))
                : qsTr("已关闭，收到通知时不会朗读")

            Switch {
                id: enableSwitch
                checked: speakerEnabled
                // RinUI 的 Switch 默认渲染 "On"/"Off"，此处改为中文
                checkedText: qsTr("开")
                uncheckedText: qsTr("关")
                onToggled: {
                    if (!ready) return
                    speakerEnabled = checked
                    backend.setEnabled(checked)
                }
            }
        }
    }

    // ═══════════════════════════════════════════
    // 引擎设置
    // ═══════════════════════════════════════════
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4

        Text {
            typography: Typography.BodyStrong
            text: qsTr("引擎设置")
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_bot_20_regular"
            title: qsTr("TTS 引擎")
            description: activeEngine !== "none"
                ? qsTr("当前使用: %1").arg(activeEngine)
                : qsTr("无可用引擎")

            ComboBox {
                id: engineCombo
                width: 180
                model: {
                    let labels = {
                        "auto": qsTr("自动（推荐）"),
                        "piper": qsTr("Piper 本地离线"),
                        "edge": qsTr("EdgeTTS 在线"),
                        "winrt": qsTr("Windows 系统语音"),
                        "pyttsx3": qsTr("pyttsx3 兜底")
                    }
                    let names = []
                    for (let i = 0; i < engineModel.length; i++) {
                        let e = engineModel[i]
                        let base = labels[e.name] || e.name
                        // 未随包分发的依赖会显示为「未安装」，与「不可用」区分开
                        let suffix = e.available === "true"
                            ? ""
                            : (e.name === "pyttsx3" ? qsTr(" (未安装)") : qsTr(" (不可用)"))
                        names.push(base + suffix)
                    }
                    return names
                }
                currentIndex: {
                    for (let i = 0; i < engineModel.length; i++) {
                        if (engineModel[i].name === currentEngine) return i
                    }
                    return 0
                }
                onCurrentIndexChanged: {
                    if (!ready) return
                    let index = currentIndex
                    if (index >= 0 && index < engineModel.length) {
                        backend.setEngine(engineModel[index].name)
                        backend.refreshVoiceList()
                    }
                }
            }
        }

        SettingCard {
            Layout.fillWidth: true
            icon.name: "ic_fluent_person_voice_20_regular"
            title: qsTr("语音")
            description: currentVoice ? currentVoice : qsTr("使用引擎默认语音")

            RowLayout {
                spacing: 8

                ComboBox {
                    id: localeCombo
                    width: 150
                    property var localeList: {
                        let locs = root.getLocales()
                        locs.unshift(qsTr("全部"))
                        return locs
                    }
                    model: localeList
                    currentIndex: {
                        if (!selectedLocale) return 0
                        for (let i = 1; i < localeList.length; i++) {
                            if (localeList[i] === selectedLocale) return i
                        }
                        return 0
                    }
                    onCurrentIndexChanged: {
                        if (!ready) return
                        let idx = currentIndex
                        if (idx <= 0) selectedLocale = ""
                        else if (idx < localeList.length) selectedLocale = localeList[idx]
                    }
                }

                ComboBox {
                    id: voiceCombo
                    width: 220
                    property var filteredVoices: {
                        if (!selectedLocale) return voiceModel
                        return root.getVoicesForLocale(selectedLocale)
                    }
                    model: {
                        let names = [qsTr("默认")]
                        for (let i = 0; i < filteredVoices.length; i++) {
                            names.push(filteredVoices[i].name)
                        }
                        return names
                    }
                    currentIndex: {
                        if (!currentVoice) return 0
                        for (let i = 0; i < filteredVoices.length; i++) {
                            if (filteredVoices[i].id === currentVoice) return i + 1
                        }
                        return 0
                    }
                    onCurrentIndexChanged: {
                        if (!ready) return
                        let idx = currentIndex
                        if (idx === 0) {
                            if (selectedLocale && filteredVoices.length > 0)
                                backend.setVoice(filteredVoices[0].id)
                            else
                                backend.setVoice("")
                        } else if (idx > 0 && idx <= filteredVoices.length) {
                            backend.setVoice(filteredVoices[idx - 1].id)
                        }
                    }
                }

                ToolButton {
                    icon.name: "ic_fluent_arrow_sync_20_regular"
                    flat: true
                    ToolTip { text: qsTr("刷新语音列表"); visible: parent.hovered }
                    onClicked: backend.refreshVoiceList()
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
        visible: piperState !== "unavailable"

        Text {
            typography: Typography.BodyStrong
            text: qsTr("离线朗读模型")
        }

        SettingExpander {
            Layout.fillWidth: true
            icon.name: "ic_fluent_cloud_off_20_regular"
            title: qsTr("Piper 本地语音模型")
            description: {
                if (piperState === "ready")
                    return qsTr("已就绪，断网也能高质量朗读")
                if (piperDownloading)
                    return qsTr("下载中 %1% · %2").arg(piperPercent).arg(piperFileName)
                return qsTr("未下载（首次约 100MB，之后完全离线）")
            }

            action: Button {
                text: piperState === "ready" ? qsTr("重新下载") : qsTr("下载")
                enabled: !piperDownloading
                icon.name: "ic_fluent_arrow_download_20_regular"
                onClicked: {
                    piperDownloading = true
                    piperPercent = 0
                    backend.downloadPiperModel()
                }
            }

            SettingItem {
                title: qsTr("离线语音")
                description: qsTr("本地神经网络合成，音质接近在线引擎，断网可用")

                contentItem: ProgressBar {
                    Layout.fillWidth: true
                    visible: piperDownloading
                    from: 0
                    to: 100
                    value: piperPercent
                }
            }

            SettingItem {
                title: qsTr("模型目录")
                description: backend ? backend.getPiperModelPath() : ""

                contentItem: Button {
                    text: qsTr("打开目录")
                    icon.name: "ic_fluent_folder_open_20_regular"
                    onClicked: {
                        let path = backend.getPiperModelPath()
                        if (path) Qt.openUrlExternally("file:///" + path.replace(/\\/g, "/"))
                    }
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

        SettingExpander {
            Layout.fillWidth: true
            icon.name: "ic_fluent_speaker_2_20_regular"
            title: qsTr("音量")
            description: qsTr("%1%").arg(Math.round(currentVolume * 100))

            action: Slider {
                id: volumeSlider
                width: 150
                from: 0
                to: 1
                stepSize: 0.05
                value: currentVolume
                onMoved: { if (backend) backend.setVolume(value) }
            }

            SettingItem {
                title: qsTr("测试朗读")
                description: qsTr("使用当前设置朗读测试文本")

                Button {
                    text: qsTr("测试")
                    icon.name: "ic_fluent_play_20_regular"
                    onClicked: {
                        let text = customTestText.trim()
                        if (!text)
                            text = qsTr("同学们好，现在开始上课。")
                        backend.testSpeak(text)
                    }
                }
            }

            SettingItem {
                title: qsTr("自定义测试文本")
                description: qsTr("留空则使用默认测试语句")

                contentItem: TextField {
                    id: testTextField
                    Layout.fillWidth: true
                    placeholderText: qsTr("同学们好，现在开始上课。")
                    text: root.customTestText
                    onTextChanged: root.customTestText = text
                }
            }
        }

        SettingExpander {
            Layout.fillWidth: true
            icon.name: "ic_fluent_rocket_20_regular"
            title: qsTr("语速")
            description: {
                let pct = Math.round(currentRate * 100)
                if (Math.abs(currentRate - 1.0) < 0.01)
                    return qsTr("正常（%1%）").arg(pct)
                return currentRate > 1.0
                    ? qsTr("偏快（%1%）").arg(pct)
                    : qsTr("偏慢（%1%）").arg(pct)
            }

            action: Slider {
                id: rateSlider
                width: 150
                from: 0.5
                to: 2.0
                stepSize: 0.1
                value: currentRate
                onMoved: { if (backend) backend.setRate(value) }
            }

            SettingItem {
                title: qsTr("语速说明")
                description: qsTr("1.0 为正常语速，数值越大越快。仅 Piper 本地引擎支持调速，在线与系统语音保持原速。")
            }
        }
    }

    // ═══════════════════════════════════════════
    // 朗读文案设置
    // ═══════════════════════════════════════════
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4

        Text {
            typography: Typography.BodyStrong
            text: qsTr("朗读文案")
        }

        Text {
            typography: Typography.Caption
            color: Colors.proxy.textSecondaryColor
            text: qsTr("可用变量: {subject} 科目, {teacher} 教师, {location} 地点, {next_subject} 下节科目, {next_teacher} 下节教师, {next_location} 下节地点, {title} 通知标题, {message} 通知正文")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        RowLayout {
            spacing: 6
            Text {
                text: qsTr("快速插入变量:")
                typography: Typography.Caption
                color: Colors.proxy.textSecondaryColor
            }
            Repeater {
                model: ["{subject}", "{teacher}", "{location}", "{next_subject}",
                        "{next_teacher}", "{next_location}", "{title}", "{message}"]

                Button {
                    text: modelData
                    flat: true
                    implicitHeight: 24
                    // 仅在用户已选中某个模板输入框时可点
                    enabled: root.activeTemplateField !== null
                    onClicked: {
                        let field = activeTemplateField
                        if (!field) return
                        // 在光标处插入变量
                        let t = field.text
                        let pos = field.cursorPosition
                        field.text = t.substring(0, pos) + modelData + t.substring(pos)
                        field.cursorPosition = pos + modelData.length
                        field.forceActiveFocus()
                        backend.setTemplate(activeTemplateKey, field.text)
                    }
                }
            }
        }

        Repeater {
            model: backend ? backend.getTemplateKeys() : []

            SettingExpander {
                Layout.fillWidth: true
                icon.name: {
                    let icons = {
                        "class": "ic_fluent_book_20_regular",
                        "break": "ic_fluent_drink_coffee_20_regular",
                        "activity": "ic_fluent_people_20_regular",
                        "free": "ic_fluent_home_20_regular",
                        "preparation": "ic_fluent_alert_20_regular"
                    }
                    return icons[modelData] || "ic_fluent_text_description_20_regular"
                }
                title: activityLabels[modelData] || modelData
                description: backend.getTemplate(modelData)

                SettingItem {
                    title: qsTr("朗读模板")

                    RowLayout {
                        spacing: 8
                        Layout.fillWidth: true

                        TextField {
                            id: templateField
                            Layout.fillWidth: true
                            // 双向绑定：外部模板变化时刷新显示框，
                            // 用户输入变化时立即写回，避免失焦前丢内容
                            text: backend.getTemplate(modelData)
                            placeholderText: backend.getDefaultTemplate(modelData)
                            onTextChanged: {
                                if (text !== backend.getTemplate(modelData))
                                    backend.setTemplate(modelData, text)
                            }
                            onActiveFocusChanged: {
                                if (activeFocus) {
                                    // 记录当前编辑目标，供「快速插入变量」使用
                                    root.activeTemplateKey = modelData
                                    root.activeTemplateField = templateField
                                } else {
                                    backend.setTemplate(modelData, text)
                                }
                            }
                        }

                        ToolButton {
                            icon.name: "ic_fluent_arrow_reset_20_regular"
                            flat: true
                            ToolTip { text: qsTr("恢复默认"); visible: parent.hovered }
                            onClicked: {
                                backend.resetTemplate(modelData)
                                templateField.text = backend.getTemplate(modelData)
                            }
                        }
                    }
                }

                SettingItem {
                    title: qsTr("试听")
                    description: qsTr("使用当前模板朗读")

                    Button {
                        text: qsTr("试听")
                        icon.name: "ic_fluent_play_20_regular"
                        onClicked: {
                            let template = backend.getTemplate(modelData)
                            let testText = template
                                .replace("{subject}", "语文")
                                .replace("{teacher}", "张老师")
                                .replace("{location}", "3号楼201")
                                .replace("{next_subject}", "数学")
                                .replace("{next_teacher}", "李老师")
                                .replace("{next_location}", "本班教室")
                                .replace("{title}", "通知标题")
                                .replace("{message}", "通知消息")
                            backend.testSpeak(testText)
                        }
                    }
                }
            }
        }
    }
}
