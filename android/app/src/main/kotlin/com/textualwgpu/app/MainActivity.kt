package com.textualwgpu.app

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
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

class MainActivity : AppCompatActivity() {

    private val prefs by lazy { getSharedPreferences("tw_profiles", Context.MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val dp = resources.displayMetrics.density
        fun px(v: Int) = (v * dp).toInt()

        val root = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#141418"))
        }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(20), px(28), px(20), px(28))
        }
        root.addView(col)

        val title = TextView(this).apply {
            text = "textual-wgpu · Remote"
            textSize = 22f
            setTextColor(Color.parseColor("#D4AD72"))
            setPadding(0, 0, 0, px(6))
        }
        val sub = TextView(this).apply {
            text = "Connect over SSH (LAN / Tailscale) to run any textual-wgpu app"
            textSize = 13f
            setTextColor(Color.parseColor("#9A9088"))
            setPadding(0, 0, 0, px(18))
        }
        col.addView(title)
        col.addView(sub)

        fun field(label: String, def: String, inputType: Int = InputType.TYPE_CLASS_TEXT): EditText {
            val lbl = TextView(this).apply {
                text = label
                textSize = 12f
                setTextColor(Color.parseColor("#C8BFB4"))
                setPadding(0, px(8), 0, px(4))
            }
            col.addView(lbl)
            return EditText(this).apply {
                setText(def)
                this.inputType = inputType
                setTextColor(Color.parseColor("#F5F0E8"))
                setHintTextColor(Color.parseColor("#706878"))
                setBackgroundColor(Color.parseColor("#221E28"))
                setPadding(px(12), px(10), px(12), px(10))
                col.addView(this)
            }
        }

        val hostEt = field("SSH Host", prefs.getString("host", "192.168.1.10") ?: "192.168.1.10")
        val portEt = field("Port", prefs.getString("port", "22") ?: "22", InputType.TYPE_CLASS_NUMBER)
        val userEt = field("Username", prefs.getString("user", "user") ?: "user")
        val passEt = field(
            "Password (optional if using key)",
            prefs.getString("pass", "") ?: "",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD,
        )
        val keyEt = field(
            "Private Key PEM (optional)",
            prefs.getString("key", "") ?: "",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE,
        ).apply {
            minLines = 2
            maxLines = 4
            hint = "-----BEGIN OPENSSH PRIVATE KEY-----"
        }
        val pyFindBody =
            "for p in python3 \"\$HOME/.local/bin/python3\" \"\$HOME/.venv/bin/python\" \"\$HOME/venv/bin/python\" " +
                "\"\$HOME/.local/share/pipx/venvs/textual-wgpu/bin/python\" " +
                "\"\$HOME\"/*/venv/bin/python \"\$HOME\"/*/.venv/bin/python " +
                "\"\$HOME\"/*/*/venv/bin/python \"\$HOME\"/*/*/.venv/bin/python " +
                "\"\$HOME\"/*/*/*/venv/bin/python \"\$HOME\"/*/*/*/.venv/bin/python; do " +
                "[ -x \"\$p\" ] || command -v \"\$p\" >/dev/null 2>&1 || continue; " +
                "\"\$p\" -c \"import textual_wgpu\" 2>/dev/null"
        val defaultDemoCmd =
            "sh -c '$pyFindBody && exec env TEXTUAL_WGPU_REMOTE=stdio \"\$p\" -m textual_wgpu; done; " +
                "echo \"textual_wgpu not found\" >&2; exit 1'"
        val defaultModuleCmd = "exec env TEXTUAL_WGPU_REMOTE=stdio python3 -m my_app"
        val defaultSocketCmd =
            "sh -c '$pyFindBody && exec \"\$p\" -m textual_wgpu.remote.cli connect-socket --socket ~/.cache/textual-wgpu/app.sock; done; " +
                "echo \"textual_wgpu not found\" >&2; exit 1'"

        val savedCmd = prefs.getString("cmd", defaultDemoCmd) ?: defaultDemoCmd
        val isLegacyDemo = savedCmd == "TEXTUAL_WGPU_REMOTE=stdio python3 -m textual_wgpu" ||
            savedCmd.startsWith("for p in python3 \"\$HOME/.venv/bin/python\"")
        val cmdEt = field(
            "Remote Command",
            if (isLegacyDemo) defaultDemoCmd else savedCmd,
        )

        val presetRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, px(10), 0, px(6))
        }
        fun presetBtn(label: String, cmd: String) {
            val b = Button(this).apply {
                text = label
                textSize = 11f
                setTextColor(Color.parseColor("#F5F0E8"))
                setBackgroundColor(Color.parseColor("#2E2738"))
                setOnClickListener { cmdEt.setText(cmd) }
            }
            val lp = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginEnd = px(6)
            }
            presetRow.addView(b, lp)
        }
        presetBtn("Demo", defaultDemoCmd)
        presetBtn("Module", defaultModuleCmd)
        presetBtn("Socket", defaultSocketCmd)
        col.addView(presetRow)

        val virtCb = CheckBox(this).apply {
            text = "Virtual Desktop (1100×720 + Pinch/Pan)"
            isChecked = prefs.getBoolean("virtual", true)
            setTextColor(Color.parseColor("#E8DFD4"))
            setPadding(0, px(10), 0, px(4))
        }
        col.addView(virtCb)

        val keysCb = CheckBox(this).apply {
            text = "Show touch key bar (disable when using mouse/keyboard)"
            isChecked = prefs.getBoolean("show_keys", true)
            setTextColor(Color.parseColor("#E8DFD4"))
            setPadding(0, px(4), 0, px(4))
        }
        col.addView(keysCb)

        val fullCb = CheckBox(this).apply {
            text = "Fullscreen mode (hide top status & system nav bars)"
            isChecked = prefs.getBoolean("fullscreen", false)
            setTextColor(Color.parseColor("#E8DFD4"))
            setPadding(0, px(4), 0, px(4))
        }
        col.addView(fullCb)

        val shareCb = CheckBox(this).apply {
            text = "Share phone Python Demo to SSH server (127.0.0.1:7420)"
            isChecked = prefs.getBoolean("share_phone", false)
            setTextColor(Color.parseColor("#E8DFD4"))
            setPadding(0, px(4), 0, px(14))
        }
        col.addView(shareCb)

        val connectBtn = Button(this).apply {
            text = "Connect over SSH"
            setTextColor(Color.parseColor("#141418"))
            setBackgroundColor(Color.parseColor("#D4AD72"))
            gravity = Gravity.CENTER
            setOnClickListener {
                val h = hostEt.text.toString().trim()
                val p = portEt.text.toString().trim().toIntOrNull() ?: 22
                val u = userEt.text.toString().trim()
                val pw = passEt.text.toString()
                val key = keyEt.text.toString().trim()
                val cmd = cmdEt.text.toString().trim()
                val virt = virtCb.isChecked
                val showKeys = keysCb.isChecked
                val fullscreen = fullCb.isChecked
                val sharePhone = shareCb.isChecked

                prefs.edit()
                    .putString("host", h)
                    .putString("port", p.toString())
                    .putString("user", u)
                    .putString("pass", pw)
                    .putString("key", key)
                    .putString("cmd", cmd)
                    .putBoolean("virtual", virt)
                    .putBoolean("show_keys", showKeys)
                    .putBoolean("fullscreen", fullscreen)
                    .putBoolean("share_phone", sharePhone)
                    .apply()

                if (sharePhone) {
                    Thread { LocalPythonHost.ensureStarted(applicationContext) }.start()
                }

                startActivity(
                    Intent(this@MainActivity, RemoteViewActivity::class.java).apply {
                        putExtra("mode", "ssh")
                        putExtra("host", h)
                        putExtra("port", p)
                        putExtra("user", u)
                        putExtra("pass", pw)
                        putExtra("key", key)
                        putExtra("cmd", cmd)
                        putExtra("virt_w", if (virt) 1100 else 0)
                        putExtra("virt_h", if (virt) 720 else 0)
                        putExtra("show_keys", showKeys)
                        putExtra("fullscreen", fullscreen)
                        putExtra("reverse_port", if (sharePhone) LocalPythonHost.PORT else 0)
                    },
                )
            }
        }
        col.addView(connectBtn)

        val localDemoBtn = Button(this).apply {
            text = "Open Local Hosted Python Demo (Offline)"
            setTextColor(Color.parseColor("#F5F0E8"))
            setBackgroundColor(Color.parseColor("#314056"))
            setOnClickListener {
                val virt = virtCb.isChecked
                startActivity(
                    Intent(this@MainActivity, RemoteViewActivity::class.java).apply {
                        putExtra("mode", "local_python")
                        putExtra("virt_w", if (virt) 1100 else 0)
                        putExtra("virt_h", if (virt) 720 else 0)
                        putExtra("show_keys", keysCb.isChecked)
                        putExtra("fullscreen", fullCb.isChecked)
                    },
                )
            }
        }
        val llp = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = px(10) }
        col.addView(localDemoBtn, llp)

        val previewBtn = Button(this).apply {
            text = "Offline Vulkan 3D Preview"
            setTextColor(Color.parseColor("#F5F0E8"))
            setBackgroundColor(Color.parseColor("#2A2436"))
            setOnClickListener {
                startActivity(
                    Intent(this@MainActivity, RemoteViewActivity::class.java).apply {
                        putExtra("mode", "preview")
                        putExtra("virt_w", 1100)
                        putExtra("virt_h", 720)
                        putExtra("show_keys", keysCb.isChecked)
                        putExtra("fullscreen", fullCb.isChecked)
                    },
                )
            }
        }
        val plp = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = px(10) }
        col.addView(previewBtn, plp)

        val nodesTitle = TextView(this).apply {
            text = "Saved Network OS Nodes (Workstation · Phone · Cloud)"
            textSize = 13f
            setTextColor(Color.parseColor("#D4AD72"))
            setPadding(0, px(18), 0, px(6))
        }
        col.addView(nodesTitle)

        val nodesBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        col.addView(nodesBox)

        fun defaultNodesJson(): JSONArray {
            return JSONArray().apply {
                put(JSONObject().apply {
                    put("id", "workstation"); put("role", "Workstation"); put("name", "Workstation GPU")
                    put("transport", "ssh"); put("host", "192.168.1.10"); put("port", 22)
                    put("user", "user"); put("cmd", defaultDemoCmd)
                })
                put(JSONObject().apply {
                    put("id", "phone"); put("role", "Phone"); put("name", "Local Phone Python")
                    put("transport", "local_python"); put("host", "127.0.0.1"); put("port", 7420)
                    put("user", "android"); put("cmd", "")
                })
                put(JSONObject().apply {
                    put("id", "cloud"); put("role", "Cloud"); put("name", "Cloud Compute Node")
                    put("transport", "ssh"); put("host", "cloud.example.net"); put("port", 22)
                    put("user", "ubuntu"); put("cmd", defaultDemoCmd)
                })
            }
        }

        fun loadNodes(): JSONArray {
            val raw = prefs.getString("saved_nodes_json", null) ?: return defaultNodesJson()
            return try {
                val arr = JSONArray(raw)
                if (arr.length() > 0) arr else defaultNodesJson()
            } catch (_: Throwable) {
                defaultNodesJson()
            }
        }

        fun refreshNodes() {
            nodesBox.removeAllViews()
            val arr = loadNodes()
            for (i in 0 until arr.length()) {
                val obj = arr.optJSONObject(i) ?: continue
                val role = obj.optString("role", "Workstation")
                val name = obj.optString("name", role)
                val transport = obj.optString("transport", "ssh")
                val host = obj.optString("host", "127.0.0.1")
                val port = obj.optInt("port", 22)
                val user = obj.optString("user", "user")
                val rawCmd = obj.optString("cmd", defaultDemoCmd)
                val cmd = if (rawCmd.startsWith("for p in python3 \"\$HOME/.venv/bin/python\"")) defaultDemoCmd else rawCmd
                val label = if (transport == "local_python") {
                    "[$role] $name · local://127.0.0.1:7420"
                } else {
                    "[$role] $name · $transport://$user@$host:$port"
                }
                val btn = Button(this).apply {
                    text = label
                    textSize = 12f
                    isAllCaps = false
                    gravity = Gravity.START or Gravity.CENTER_VERTICAL
                    setTextColor(Color.parseColor("#F5F0E8"))
                    setBackgroundColor(Color.parseColor("#24202D"))
                    setPadding(px(12), px(8), px(12), px(8))
                    setOnClickListener {
                        if (transport == "local_python") {
                            localDemoBtn.performClick()
                        } else {
                            hostEt.setText(host)
                            portEt.setText(port.toString())
                            userEt.setText(user)
                            if (cmd.isNotEmpty()) cmdEt.setText(cmd)
                            if (transport == "tcp") {
                                startActivity(
                                    Intent(this@MainActivity, RemoteViewActivity::class.java).apply {
                                        putExtra("mode", "tcp")
                                        putExtra("host", host)
                                        putExtra("port", port)
                                        putExtra("virt_w", if (virtCb.isChecked) 1100 else 0)
                                        putExtra("virt_h", if (virtCb.isChecked) 720 else 0)
                                        putExtra("show_keys", keysCb.isChecked)
                                        putExtra("fullscreen", fullCb.isChecked)
                                    },
                                )
                            } else {
                                connectBtn.performClick()
                            }
                        }
                    }
                    setOnLongClickListener {
                        val next = JSONArray()
                        for (j in 0 until arr.length()) {
                            if (j != i) next.put(arr.get(j))
                        }
                        prefs.edit().putString("saved_nodes_json", next.toString()).apply()
                        refreshNodes()
                        true
                    }
                }
                val nlp = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = px(6) }
                nodesBox.addView(btn, nlp)
            }
        }
        refreshNodes()

        val saveNodeBtn = Button(this).apply {
            text = "+ Save Current Host as Node (Workstation / Phone / Cloud)"
            textSize = 12f
            isAllCaps = false
            setTextColor(Color.parseColor("#D4AD72"))
            setBackgroundColor(Color.parseColor("#1F1B26"))
            setOnClickListener {
                val roles = arrayOf("Workstation", "Phone", "Cloud")
                AlertDialog.Builder(this@MainActivity)
                    .setTitle("Select Node Role")
                    .setItems(roles) { _, which ->
                        val role = roles[which]
                        val h = hostEt.text.toString().trim().ifEmpty { "127.0.0.1" }
                        val p = portEt.text.toString().trim().toIntOrNull() ?: 22
                        val u = userEt.text.toString().trim()
                        val c = cmdEt.text.toString().trim()
                        val arr = loadNodes()
                        arr.put(JSONObject().apply {
                            put("id", "${role.lowercase()}_${arr.length() + 1}")
                            put("role", role)
                            put("name", "$role ($h)")
                            put("transport", "ssh")
                            put("host", h)
                            put("port", p)
                            put("user", u)
                            put("cmd", c)
                        })
                        prefs.edit().putString("saved_nodes_json", arr.toString()).apply()
                        refreshNodes()
                    }
                    .show()
            }
        }
        val snlp = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = px(8) }
        col.addView(saveNodeBtn, snlp)

        setContentView(root)
    }
}
