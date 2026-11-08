-- Lanzador de MacRAR.app
-- Doble clic abre la ventana; soltar archivos comprimidos sobre el icono (o "Abrir con") los abre.
-- Ejecuta macrar.py, que build_app.sh copia dentro de la app (Contents/Resources).

on run
	launchMacRAR("")
end run

on open theItems
	repeat with f in theItems
		launchMacRAR(quoted form of POSIX path of f)
	end repeat
end open

on launchMacRAR(args)
	set scriptPath to POSIX path of (path to me) & "Contents/Resources/macrar.py"
	set logPath to POSIX path of (path to home folder) & "Library/Logs/MacRAR.log"
	do shell script "/usr/bin/python3 " & quoted form of scriptPath & " " & args & " > " & quoted form of logPath & " 2>&1 &"
end launchMacRAR
