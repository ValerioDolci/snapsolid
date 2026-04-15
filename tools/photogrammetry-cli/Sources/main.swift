/// Snapsolid Photogrammetry CLI
/// Basato sul sample code Apple "Creating a Photogrammetry Command-Line App"
/// Output: OBJ (mesh per il cleaning) o USDZ

import ArgumentParser
import Foundation
import RealityKit

private typealias Configuration = PhotogrammetrySession.Configuration
private typealias Request = PhotogrammetrySession.Request

struct PhotogrammetryCLI: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Ricostruisce mesh 3D da una cartella di foto (Apple Object Capture)")

    @Argument(help: "Cartella contenente le foto (JPEG/HEIC/PNG)")
    var inputFolder: String

    @Argument(help: "File output (.obj o .usdz)")
    var outputFile: String

    @Option(name: .shortAndLong,
            help: "Livello di dettaglio: preview, reduced, medium, full, raw")
    var detail: String = "medium"

    @Option(name: [.customShort("o"), .long],
            help: "Ordine foto: unordered, sequential")
    var ordering: String = "unordered"

    @Option(name: .shortAndLong,
            help: "Sensibilita' feature: normal, high")
    var sensitivity: String = "normal"

    @Flag(name: .long,
          help: "Abilita object masking (isola oggetto dallo sfondo)")
    var objectMasking: Bool = false

    func run() throws {
        let inputUrl = URL(fileURLWithPath: inputFolder, isDirectory: true)
        let outputUrl = URL(fileURLWithPath: outputFile)

        // Verifica che la cartella esista
        guard FileManager.default.fileExists(atPath: inputFolder) else {
            print("ERRORE: Cartella non trovata: \(inputFolder)")
            Foundation.exit(1)
        }

        // Configurazione
        var config = Configuration()
        switch ordering {
        case "sequential": config.sampleOrdering = .sequential
        default: config.sampleOrdering = .unordered
        }
        switch sensitivity {
        case "high": config.featureSensitivity = .high
        default: config.featureSensitivity = .normal
        }

        if objectMasking {
            config.isObjectMaskingEnabled = true
            print("Object masking: ABILITATO")
        }

        print("Input:  \(inputFolder)")
        print("Output: \(outputFile)")
        print("Detail: \(detail)")

        // Crea la sessione
        let session: PhotogrammetrySession
        do {
            session = try PhotogrammetrySession(input: inputUrl, configuration: config)
            print("Sessione creata.")
        } catch {
            print("ERRORE creazione sessione: \(error)")
            Foundation.exit(1)
        }

        // Gestisci output async
        Task {
            do {
                for try await output in session.outputs {
                    switch output {
                    case .requestProgress(_, fractionComplete: let fraction):
                        let pct = Int(fraction * 100)
                        print("Progresso: \(pct)%")
                    case .requestComplete(_, let result):
                        switch result {
                        case .modelFile(let url):
                            print("Modello salvato: \(url.path)")
                        default:
                            print("Risultato: \(result)")
                        }
                    case .requestError(_, let error):
                        print("ERRORE: \(error)")
                        Foundation.exit(1)
                    case .processingComplete:
                        print("Completato!")
                        Foundation.exit(0)
                    case .inputComplete:
                        print("Foto caricate, inizio elaborazione...")
                    case .invalidSample(let id, let reason):
                        print("Foto invalida: id=\(id) motivo=\(reason)")
                    case .skippedSample(let id):
                        print("Foto saltata: id=\(id)")
                    case .automaticDownsampling:
                        print("Downsampling automatico applicato")
                    default:
                        break
                    }
                }
            } catch {
                print("ERRORE output: \(error)")
                Foundation.exit(1)
            }
        }

        // Avvia la ricostruzione
        withExtendedLifetime(session) {
            do {
                let detailLevel = try Request.Detail(detail)
                let request = Request.modelFile(url: outputUrl, detail: detailLevel)
                try session.process(requests: [request])
                RunLoop.main.run()
            } catch {
                print("ERRORE processo: \(error)")
                Foundation.exit(1)
            }
        }
    }
}

// MARK: - Detail parsing

extension PhotogrammetrySession.Request.Detail {
    init(_ string: String) throws {
        switch string.lowercased() {
        case "preview": self = .preview
        case "reduced": self = .reduced
        case "medium": self = .medium
        case "full": self = .full
        case "raw": self = .raw
        default:
            print("Dettaglio invalido: \(string). Usa: preview, reduced, medium, full, raw")
            throw ExitCode.failure
        }
    }
}

PhotogrammetryCLI.main()
