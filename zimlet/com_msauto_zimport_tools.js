function com_msauto_zimport_tools_HandlerObject() {}
com_msauto_zimport_tools_HandlerObject.prototype = new ZmZimletBase();
com_msauto_zimport_tools_HandlerObject.prototype.constructor =
    com_msauto_zimport_tools_HandlerObject;

var Zit = com_msauto_zimport_tools_HandlerObject;

Zit.APP_NAME = "ZIMPORT_TOOLS";
Zit.IFRAME_SRC = "/zimport-tools/";

Zit.prototype.init = function() {
    // Register an application in the top app chooser.
    // The exact ZmApp registration API on Zimbra 8.8.15 may need adjustment;
    // see docs spec §13 ("待细化"). Below is the standard classic pattern.
    var app = appCtxt.getApp(Zit.APP_NAME);
    if (!app) {
        ZmApp.registerApp(Zit.APP_NAME, {
            nameKey:           "数据导入",
            icon:              "ZimletAlertImg",
            chooserTooltipKey: "数据导入",
            viewTooltipKey:    "数据导入",
            defaultSort:       Number.MAX_VALUE,
            chooserSort:       Number.MAX_VALUE
        });
    }
};

Zit.prototype.appActive = function(appName, active) {
    if (appName !== Zit.APP_NAME || !active) return;
    if (document.getElementById("zit-iframe-host")) return;
    var iframe = document.createElement("iframe");
    iframe.id = "zit-iframe-host";
    iframe.src = Zit.IFRAME_SRC;
    iframe.className = "zit-iframe";
    var shell = appCtxt.getShell();
    shell.getHtmlElement().appendChild(iframe);
};
