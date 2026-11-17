package com.textualwgpu.app

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.wifi.WifiManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.InetAddress
import java.net.MulticastSocket
import java.net.SocketTimeoutException
import java.util.concurrent.ConcurrentHashMap

class MainActivity : AppCompatActivity() {

    private val prefs by lazy { getSharedPreferences("tw_profiles", Context.MODE_PRIVATE) }
    private val mainHandler = Handler(Looper.getMainLooper())
    private lateinit var nodesBox: LinearLayout
    private lateinit var statusTv: TextView

    private data class LivePeer(
        val host: String,
        val name: String,
        val sshPort: Int,
        val tcpPort: Int,
        val appTitle: String,
        val seenAtMs: Long,
    )

    private val livePeers = ConcurrentHashMap<String, LivePeer>()
    @Volatile
    private var discoveryRunning = false
    private var discoveryThread: Thread? = null
    private var multicastLock: WifiManager.MulticastLock? = null

    private fun px(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#141418"))
            setPadding(px(16), px(20), px(16), px(16))
        }

        // Minimal top header: title + compact (+) and (⚙) actions
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, 0, 0, px(6))
        }
        val titleCol = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        val title = TextView(this).apply {
            text = "textual-wgpu"
            textSize = 22f
            setTextColor(Color.parseColor("#D4AD72"))
        }
        statusTv = TextView(this).apply {
            text = "Scanning LAN · tap any node to connect"
            textSize = 12f
            setTextColor(Color.parseColor("#9A9088"))
        }
        titleCol.addView(title)
        titleCol.addView(statusTv)
        header.addView(titleCol, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))

        fun iconBtn(label: String, onClick: () -> Unit): Button {
            return Button(this).apply {
                text = label
                textSize = 14f
                isAllCaps = false
                setTextColor(Color.parseColor("#F5F0E8"))
                setBackgroundColor(Color.parseColor("#262130"))
                setPadding(px(12), px(6), px(12), px(6))
                setOnClickListener { onClick() }
            }
        }
        val addBtn = iconBtn("+ Node") { showEditNodeDialog(null) }
        val cfgBtn = iconBtn("⚙") { showSettingsDialog() }
        header.addView(
            addBtn,
            LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { marginEnd = px(8) },
        )
        header.addView(
            cfgBtn,
            LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT),
        )
        root.addView(header)

        val scroll = ScrollView(this).apply {
            setPadding(0, px(8), 0, 0)
        }
        nodesBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        scroll.addView(nodesBox)
        root.addView(scroll, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))

        setContentView(root)
        refreshNodesList()
    }

    override fun onResume() {
        super.onResume()
        startLanDiscovery()
        refreshNodesList()
    }

    override fun onPause() {
        super.onPause()
        stopLanDiscovery()
    }

    private fun loadSavedNodes(): MutableList<JSONObject> {
        val raw = prefs.getString("saved_nodes_v2", null)
        if (raw != null) {
            try {
                val arr = JSONArray(raw)
                val out = mutableListOf<JSONObject>()
                for (i in 0 until arr.length()) {
                    arr.optJSONObject(i)?.let { out.add(it) }
                }
                return out
            } catch (_: Throwable) {
            }
        }
        // Migrate legacy single-host profile if user previously connected
        val legacyHost = prefs.getString("host", "")?.trim().orEmpty()
        val out = mutableListOf<JSONObject>()
        if (legacyHost.isNotEmpty() && legacyHost != "192.168.1.10") {
            out.add(
                JSONObject().apply {
                    put("id", "saved_1")
                    put("name", legacyHost)
                    put("host", legacyHost)
                    put("port", prefs.getString("port", "22")?.toIntOrNull() ?: 22)
                    put("user", prefs.getString("user", "") ?: "")
                    put("pass", prefs.getString("pass", "") ?: "")
                    put("key", prefs.getString("key", "") ?: "")
                },
            )
            saveNodes(out)
        }
        return out
    }

    private fun saveNodes(nodes: List<JSONObject>) {
        val arr = JSONArray()
        for (n in nodes) arr.put(n)
        prefs.edit().putString("saved_nodes_v2", arr.toString()).apply()
    }

    private fun upsertSavedNode(
        host: String,
        port: Int,
        name: String,
        user: String,
        pass: String,
        key: String,
    ) {
        val nodes = loadSavedNodes()
        val existing = nodes.firstOrNull { it.optString("host") == host && it.optInt("port", 22) == port }
        val obj = existing ?: JSONObject().apply {
            put("id", "node_${System.currentTimeMillis()}")
            nodes.add(0, this)
        }
        obj.put("name", name.ifBlank { host })
        obj.put("host", host)
        obj.put("port", port)
        obj.put("user", user)
        obj.put("pass", pass)
        obj.put("key", key)
        saveNodes(nodes)
        // Remember last credentials as convenient fallback for newly discovered LAN nodes
        if (user.isNotBlank()) prefs.edit().putString("last_user", user).apply()
        if (pass.isNotBlank()) prefs.edit().putString("last_pass", pass).apply()
        if (key.isNotBlank()) prefs.edit().putString("last_key", key).apply()
    }

    private fun startLanDiscovery() {
        if (discoveryRunning) return
        discoveryRunning = true
        try {
            val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            multicastLock = wm?.createMulticastLock("tw_lan_disc")?.apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (_: Throwable) {
        }
        discoveryThread = Thread({
            var sock: MulticastSocket? = null
            try {
                sock = MulticastSocket(7421).apply {
                    reuseAddress = true
                    soTimeout = 1000
                    try {
                        joinGroup(InetAddress.getByName("239.255.74.20"))
                    } catch (_: Throwable) {
                    }
                }
                val buf = ByteArray(2048)
                while (discoveryRunning) {
                    val pkt = DatagramPacket(buf, buf.size)
                    try {
                        sock.receive(pkt)
                        val srcIp = pkt.address?.hostAddress ?: continue
                        val jsonStr = String(pkt.data, 0, pkt.length, Charsets.UTF_8)
                        val obj = JSONObject(jsonStr)
                        val name = obj.optString("name", srcIp)
                        val sshPort = obj.optInt("ssh_port", 22)
                        val tcpPort = obj.optInt("tcp_port", 7420)
                        val app = obj.optString("app", "textual-wgpu")
                        livePeers[srcIp] = LivePeer(
                            host = srcIp,
                            name = name,
                            sshPort = sshPort,
                            tcpPort = tcpPort,
                            appTitle = app,
                            seenAtMs = System.currentTimeMillis(),
                        )
                        mainHandler.post { refreshNodesList() }
                    } catch (_: SocketTimeoutException) {
                        val now = System.currentTimeMillis()
                        var removed = false
                        for ((k, v) in livePeers) {
                            if (now - v.seenAtMs > 5500L) {
                                livePeers.remove(k)
                                removed = true
                            }
                        }
                        if (removed) mainHandler.post { refreshNodesList() }
                    } catch (_: Throwable) {
                    }
                }
            } catch (_: Throwable) {
            } finally {
                try {
                    sock?.close()
                } catch (_: Throwable) {
                }
            }
        }, "tw-lan-disc").apply {
            isDaemon = true
            start()
        }
    }

    private fun stopLanDiscovery() {
        discoveryRunning = false
        discoveryThread?.interrupt()
        discoveryThread = null
        try {
            if (multicastLock?.isHeld == true) multicastLock?.release()
        } catch (_: Throwable) {
        }
        multicastLock = null
    }

    private fun refreshNodesList() {
        nodesBox.removeAllViews()
        val saved = loadSavedNodes()
        val now = System.currentTimeMillis()
        val activeByHost = livePeers.values.filter { now - it.seenAtMs <= 6000L }.associateBy { it.host }
        val seenHosts = HashSet<String>()

        fun addNodeRow(
            title: String,
            subtitle: String,
            accentBg: String,
            onTap: () -> Unit,
            onLongPress: (() -> Unit)? = null,
        ) {
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setBackgroundColor(Color.parseColor(accentBg))
                setPadding(px(14), px(12), px(14), px(12))
                isClickable = true
                setOnClickListener { onTap() }
                if (onLongPress != null) {
                    setOnLongClickListener {
                        onLongPress()
                        true
                    }
                }
            }
            val tvTitle = TextView(this).apply {
                text = title
                textSize = 15f
                setTextColor(Color.parseColor("#F5F0E8"))
            }
            val tvSub = TextView(this).apply {
                text = subtitle
                textSize = 12f
                setTextColor(Color.parseColor("#B4ABA0"))
                setPadding(0, px(2), 0, 0)
            }
            card.addView(tvTitle)
            card.addView(tvSub)
            val lp = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = px(8) }
            nodesBox.addView(card, lp)
        }

        // 1) Live discovered LAN servers first (1-tap attach if running!)
        for (peer in activeByHost.values.sortedBy { it.name.lowercase() }) {
            seenHosts.add(peer.host)
            val savedMatch = saved.firstOrNull { it.optString("host") == peer.host }
            val user = savedMatch?.optString("user", "").orEmpty()
            val pass = savedMatch?.optString("pass", "").orEmpty()
            val key = savedMatch?.optString("key", "").orEmpty()
            addNodeRow(
                title = "🟢  ${peer.name}  (${peer.host})",
                subtitle = "Running: ${peer.appTitle} · Tap to connect",
                accentBg = "#202A24",
                onTap = { connectOrPromptSsh(peer.host, peer.sshPort, peer.name, user, pass, key) },
                onLongPress = savedMatch?.let { obj -> { showNodeActions(obj) } },
            )
        }

        // 2) Saved offline/WAN SSH nodes (1-tap SSH connect & auto-start!)
        for (obj in saved) {
            val host = obj.optString("host", "").trim()
            if (host.isEmpty() || seenHosts.contains(host)) continue
            val name = obj.optString("name", host)
            val port = obj.optInt("port", 22)
            val user = obj.optString("user", "")
            val pass = obj.optString("pass", "")
            val key = obj.optString("key", "")
            addNodeRow(
                title = "⚪  $name  ($host:$port)",
                subtitle = if (user.isNotEmpty()) "$user@$host · Tap to connect & start" else "Tap to sign in over SSH",
                accentBg = "#221E2A",
                onTap = { connectOrPromptSsh(host, port, name, user, pass, key) },
                onLongPress = { showNodeActions(obj) },
            )
        }

        // 3) Local device Python host (always available offline)
        addNodeRow(
            title = "📱  This Phone (Local Python)",
            subtitle = "Run built-in demo offline on device GPU",
            accentBg = "#1F2633",
            onTap = { launchLocalPhoneDemo() },
        )

        val liveCount = activeByHost.size
        statusTv.text = if (liveCount > 0) {
            "$liveCount running on LAN · tap to connect"
        } else {
            "Scanning LAN · tap any node to connect & start"
        }
    }

    private fun connectOrPromptSsh(
        host: String,
        port: Int,
        name: String,
        savedUser: String,
        savedPass: String,
        savedKey: String,
    ) {
        val user = savedUser.ifBlank { prefs.getString("last_user", "") ?: "" }
        val pass = savedPass.ifBlank { prefs.getString("last_pass", "") ?: "" }
        val key = savedKey.ifBlank { prefs.getString("last_key", "") ?: "" }
        if (user.isNotBlank() && (pass.isNotBlank() || key.isNotBlank())) {
            upsertSavedNode(host, port, name, user, pass, key)
            launchSsh(host, port, user, pass, key)
            return
        }
        // Prompt once for SSH credentials on a newly discovered host
        showEditNodeDialog(
            JSONObject().apply {
                put("name", name)
                put("host", host)
                put("port", port)
                put("user", user)
                put("pass", pass)
                put("key", key)
            },
            connectOnSave = true,
        )
    }

    private fun launchSsh(host: String, port: Int, user: String, pass: String, key: String) {
        val virt = prefs.getBoolean("virtual", true)
        val showKeys = prefs.getBoolean("show_keys", true)
        val fullscreen = prefs.getBoolean("fullscreen", false)
        val sharePhone = prefs.getBoolean("share_phone", false)
        val customCmd = prefs.getString("custom_cmd", "")?.trim().orEmpty()

        if (sharePhone) {
            Thread { LocalPythonHost.ensureStarted(applicationContext) }.start()
        }

        startActivity(
            Intent(this, RemoteViewActivity::class.java).apply {
                putExtra("mode", "ssh")
                putExtra("host", host)
                putExtra("port", port)
                putExtra("user", user)
                putExtra("pass", pass)
                putExtra("key", key)
                putExtra("cmd", customCmd)
                putExtra("virt_w", if (virt) 1100 else 0)
                putExtra("virt_h", if (virt) 720 else 0)
                putExtra("show_keys", showKeys)
                putExtra("fullscreen", fullscreen)
                putExtra("reverse_port", if (sharePhone) 7422 else 0)
            },
        )
    }

    private fun launchLocalPhoneDemo() {
        val virt = prefs.getBoolean("virtual", true)
        startActivity(
            Intent(this, RemoteViewActivity::class.java).apply {
                putExtra("mode", "local_python")
                putExtra("virt_w", if (virt) 1100 else 0)
                putExtra("virt_h", if (virt) 720 else 0)
                putExtra("show_keys", prefs.getBoolean("show_keys", true))
                putExtra("fullscreen", prefs.getBoolean("fullscreen", false))
            },
        )
    }

    private fun showNodeActions(obj: JSONObject) {
        val host = obj.optString("host")
        val name = obj.optString("name", host)
        AlertDialog.Builder(this)
            .setTitle(name)
            .setItems(arrayOf("Connect", "Edit SSH Credentials", "Remove Node")) { _, which ->
                when (which) {
                    0 -> connectOrPromptSsh(
                        host,
                        obj.optInt("port", 22),
                        name,
                        obj.optString("user", ""),
                        obj.optString("pass", ""),
                        obj.optString("key", ""),
                    )
                    1 -> showEditNodeDialog(obj, connectOnSave = false)
                    2 -> {
                        val nodes = loadSavedNodes().filterNot {
                            it.optString("host") == host && it.optInt("port", 22) == obj.optInt("port", 22)
                        }
                        saveNodes(nodes)
                        refreshNodesList()
                    }
                }
            }
            .show()
    }

    private fun showEditNodeDialog(existing: JSONObject?, connectOnSave: Boolean = true) {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(18), px(12), px(18), px(4))
        }
        fun addEt(hintTxt: String, initVal: String, inputType: Int = InputType.TYPE_CLASS_TEXT): EditText {
            return EditText(this).apply {
                hint = hintTxt
                setText(initVal)
                this.inputType = inputType
                box.addView(this)
            }
        }
        val nameEt = addEt("Name (optional)", existing?.optString("name", "") ?: "")
        val hostEt = addEt("SSH Host / IP", existing?.optString("host", "") ?: "")
        val portEt = addEt("SSH Port", (existing?.optInt("port", 22) ?: 22).toString(), InputType.TYPE_CLASS_NUMBER)
        val userEt = addEt("Username", existing?.optString("user", prefs.getString("last_user", "") ?: "") ?: "")
        val passEt = addEt(
            "Password (optional if using key)",
            existing?.optString("pass", "") ?: "",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD,
        )
        val keyEt = addEt(
            "Private Key PEM (optional)",
            existing?.optString("key", prefs.getString("last_key", "") ?: "") ?: "",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE,
        ).apply {
            minLines = 2
            maxLines = 4
        }

        AlertDialog.Builder(this)
            .setTitle(if (existing == null) "Add SSH Node" else "SSH Node")
            .setView(box)
            .setPositiveButton(if (connectOnSave) "Connect" else "Save") { _, _ ->
                val h = hostEt.text.toString().trim()
                if (h.isEmpty()) return@setPositiveButton
                val p = portEt.text.toString().trim().toIntOrNull() ?: 22
                val n = nameEt.text.toString().trim().ifEmpty { h }
                val u = userEt.text.toString().trim()
                val pw = passEt.text.toString()
                val k = keyEt.text.toString().trim()
                upsertSavedNode(h, p, n, u, pw, k)
                refreshNodesList()
                if (connectOnSave && u.isNotEmpty() && (pw.isNotEmpty() || k.isNotEmpty())) {
                    launchSsh(h, p, u, pw, k)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showSettingsDialog() {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(18), px(12), px(18), px(4))
        }
        val virtCb = CheckBox(this).apply {
            text = "Virtual Desktop (1100×720 + Pinch/Pan)"
            isChecked = prefs.getBoolean("virtual", true)
            box.addView(this)
        }
        val keysCb = CheckBox(this).apply {
            text = "Show touch key bar"
            isChecked = prefs.getBoolean("show_keys", true)
            box.addView(this)
        }
        val fullCb = CheckBox(this).apply {
            text = "Fullscreen mode"
            isChecked = prefs.getBoolean("fullscreen", false)
            box.addView(this)
        }
        val shareCb = CheckBox(this).apply {
            text = "Share Phone Demo to Workstation (127.0.0.1:7422)"
            isChecked = prefs.getBoolean("share_phone", false)
            box.addView(this)
        }
        val cmdEt = EditText(this).apply {
            hint = "Custom remote SSH command (leave blank for auto)"
            setText(prefs.getString("custom_cmd", "") ?: "")
            box.addView(this)
        }
        val logsBtn = Button(this).apply {
            text = "View Debug & Perf Logs"
            isAllCaps = false
            setOnClickListener {
                val pyLogFile = java.io.File(cacheDir.parentFile, ".cache/textual-wgpu/remote.log")
                val pyLogs = if (pyLogFile.isFile) pyLogFile.readText().takeLast(4000) else ""
                val msg = buildString {
                    append("=== Android Client / Perf Logs ===\n")
                    append(TwLog.dump())
                    if (pyLogs.isNotBlank()) {
                        append("\n\n=== Local Python Server Logs ===\n")
                        append(pyLogs)
                    }
                }
                val tv = TextView(this@MainActivity).apply {
                    text = msg
                    textSize = 10f
                    setTextIsSelectable(true)
                    setPadding(px(12), px(8), px(12), px(8))
                }
                val sv = ScrollView(this@MainActivity).apply { addView(tv) }
                AlertDialog.Builder(this@MainActivity)
                    .setTitle("Debug & Performance Logs")
                    .setView(sv)
                    .setPositiveButton("Close", null)
                    .show()
            }
            box.addView(this)
        }

        AlertDialog.Builder(this)
            .setTitle("Display & Connection Settings")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                prefs.edit()
                    .putBoolean("virtual", virtCb.isChecked)
                    .putBoolean("show_keys", keysCb.isChecked)
                    .putBoolean("fullscreen", fullCb.isChecked)
                    .putBoolean("share_phone", shareCb.isChecked)
                    .putString("custom_cmd", cmdEt.text.toString().trim())
                    .apply()
            }
            .setNeutralButton("Offline 3D Preview") { _, _ ->
                startActivity(
                    Intent(this, RemoteViewActivity::class.java).apply {
                        putExtra("mode", "preview")
                        putExtra("virt_w", 1100)
                        putExtra("virt_h", 720)
                        putExtra("show_keys", keysCb.isChecked)
                        putExtra("fullscreen", fullCb.isChecked)
                    },
                )
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
