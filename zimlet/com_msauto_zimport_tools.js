function com_msauto_zimport_tools_HandlerObject() {}
com_msauto_zimport_tools_HandlerObject.prototype = new ZmZimletBase();
com_msauto_zimport_tools_HandlerObject.prototype.constructor =
    com_msauto_zimport_tools_HandlerObject;

var Zit = com_msauto_zimport_tools_HandlerObject;
Zit.IFRAME_SRC = "/zimport-tools/";
Zit.OVERLAY_ID = "zit-overlay";

Zit._open = function() {
    var existing = document.getElementById(Zit.OVERLAY_ID);
    if (existing) {
        existing.style.display = "block";
        return;
    }
    var overlay = document.createElement("div");
    overlay.id = Zit.OVERLAY_ID;
    overlay.className = "zit-overlay";

    var bar = document.createElement("div");
    bar.className = "zit-bar";
    bar.textContent = "数据导入";

    var close = document.createElement("button");
    close.className = "zit-close";
    close.textContent = "× 关闭";
    close.onclick = function() {
        overlay.style.display = "none";
    };
    bar.appendChild(close);

    var iframe = document.createElement("iframe");
    iframe.className = "zit-iframe";
    iframe.src = Zit.IFRAME_SRC;

    overlay.appendChild(bar);
    overlay.appendChild(iframe);
    document.body.appendChild(overlay);
};

Zit.prototype.singleClicked = function() { Zit._open(); };
Zit.prototype.doubleClicked = function() { Zit._open(); };
