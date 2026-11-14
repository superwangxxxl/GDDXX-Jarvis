// JarvisLocate: asks macOS Location Services for one position fix and writes it as JSON to the file named by --out.
// Built by jarvis/platforms/maclocate.py into a tiny app bundle of its own, because macOS grants location access per
// app (bundle id + Info.plist usage text), which a bare Python interpreter does not have.
import AppKit
import CoreLocation

final class Locator: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private let outPath: String
    private var finished = false
    private var asked = false
    private var requested = false

    init(outPath: String) {
        self.outPath = outPath
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func start() {
        if !CLLocationManager.locationServicesEnabled() {
            finish(["ok": false, "reason": "services_off"])
            return
        }
        handle(manager.authorizationStatus)
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        handle(manager.authorizationStatus)
    }

    private func handle(_ status: CLAuthorizationStatus) {
        if finished { return }
        switch status {
        case .notDetermined:
            if !asked {
                asked = true
                manager.requestWhenInUseAuthorization()
            }
        case .restricted:
            finish(["ok": false, "reason": "restricted"])
        case .denied:
            finish(["ok": false, "reason": "denied"])
        case .authorizedAlways, .authorizedWhenInUse:
            if !requested {
                requested = true
                manager.requestLocation()
            }
        @unknown default:
            finish(["ok": false, "reason": "unavailable"])
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard !finished, let loc = locations.last, loc.horizontalAccuracy >= 0 else { return }
        var out: [String: Any] = [
            "ok": true,
            "lat": loc.coordinate.latitude,
            "lon": loc.coordinate.longitude,
            "accuracy": loc.horizontalAccuracy,
            "ts": Date().timeIntervalSince1970,
        ]
        CLGeocoder().reverseGeocodeLocation(loc) { [weak self] marks, _ in
            if let p = marks?.first {
                out["name"] = p.locality ?? p.subAdministrativeArea ?? p.administrativeArea ?? ""
            }
            self?.finish(out)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
            self?.finish(out)
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        if let e = error as? CLError, e.code == .denied {
            finish(["ok": false, "reason": "denied"])
        } else {
            finish(["ok": false, "reason": "unavailable"])
        }
    }

    func finish(_ result: [String: Any]) {
        if finished { return }
        finished = true
        if let data = try? JSONSerialization.data(withJSONObject: result) {
            try? data.write(to: URL(fileURLWithPath: outPath), options: .atomic)
        }
        exit(0)
    }
}

let args = CommandLine.arguments
func argument(_ name: String) -> String? {
    if let i = args.firstIndex(of: name), i + 1 < args.count { return args[i + 1] }
    return nil
}

guard let outPath = argument("--out") else { exit(64) }
let limit = Double(argument("--timeout") ?? "") ?? 60
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let locator = Locator(outPath: outPath)
DispatchQueue.main.async { locator.start() }
DispatchQueue.main.asyncAfter(deadline: .now() + limit) { locator.finish(["ok": false, "reason": "timeout"]) }
app.run()
