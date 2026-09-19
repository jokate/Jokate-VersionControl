// Jokate revision control plugin for Unreal Engine.

#include "JokateHttp.h"

#include "JokateSourceControlLog.h"
#include "Dom/JsonObject.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "HAL/Event.h"
#include "HAL/PlatformProcess.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

#define LOCTEXT_NAMESPACE "JokateSourceControl"

FJokateHttpResult FJokateHttp::GetJson(const FString& BaseUrl, const FString& Path, float TimeoutSeconds)
{
	return Request(TEXT("GET"), BaseUrl, Path, FString(), TimeoutSeconds);
}

FJokateHttpResult FJokateHttp::PostJson(const FString& BaseUrl, const FString& Path, const TSharedPtr<FJsonObject>& Body, float TimeoutSeconds)
{
	FString BodyText(TEXT("{}"));
	if (Body.IsValid())
	{
		BodyText.Reset();
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&BodyText);
		FJsonSerializer::Serialize(Body.ToSharedRef(), Writer);
	}
	return Request(TEXT("POST"), BaseUrl, Path, BodyText, TimeoutSeconds);
}

FJokateHttpResult FJokateHttp::Request(const FString& Verb, const FString& BaseUrl, const FString& Path, const FString& Body, float TimeoutSeconds)
{
	FJokateHttpResult Result;

	const FString Url = BaseUrl + Path;
	const TSharedRef<IHttpRequest, ESPMode::ThreadSafe> HttpRequest = FHttpModule::Get().CreateRequest();
	HttpRequest->SetURL(Url);
	HttpRequest->SetVerb(Verb);
	HttpRequest->SetHeader(TEXT("Content-Type"), TEXT("application/json"));
	HttpRequest->SetTimeout(TimeoutSeconds);
	if (!Body.IsEmpty())
	{
		HttpRequest->SetContentAsString(Body);
	}

	// 완료 콜백을 HTTP 스레드에서 받는다 — 게임 스레드 틱에 의존하지 않으므로 여기서 기다려도 안전하다.
	HttpRequest->SetDelegateThreadPolicy(EHttpRequestDelegateThreadPolicy::CompleteOnHttpThread);

	FEvent* DoneEvent = FPlatformProcess::GetSynchEventFromPool(true);
	bool bSucceeded = false;
	int32 StatusCode = 0;
	FString ResponseText;

	HttpRequest->OnProcessRequestComplete().BindLambda(
		[DoneEvent, &bSucceeded, &StatusCode, &ResponseText](FHttpRequestPtr /*InRequest*/, FHttpResponsePtr InResponse, bool bInSucceeded)
		{
			if (bInSucceeded && InResponse.IsValid())
			{
				bSucceeded = true;
				StatusCode = InResponse->GetResponseCode();
				ResponseText = InResponse->GetContentAsString();
			}
			DoneEvent->Trigger();
		});

	if (!HttpRequest->ProcessRequest())
	{
		FPlatformProcess::ReturnSynchEventToPool(DoneEvent);
		Result.ErrorText = LOCTEXT("JokateHttpStartFailed", "Jokate 데몬에 요청을 보내지 못했습니다.");
		return Result;
	}

	const uint32 WaitMs = static_cast<uint32>(TimeoutSeconds * 1000.0f) + 2000;
	const bool bCompleted = DoneEvent->Wait(WaitMs);
	if (!bCompleted)
	{
		HttpRequest->CancelRequest();
		// 콜백이 참조 캡처를 건드리지 못하도록 조금 더 기다린다.
		DoneEvent->Wait(2000);
	}
	HttpRequest->OnProcessRequestComplete().Unbind();
	FPlatformProcess::ReturnSynchEventToPool(DoneEvent);

	if (!bCompleted || !bSucceeded)
	{
		Result.ErrorText = FText::Format(
			LOCTEXT("JokateHttpNoResponse", "Jokate 데몬이 응답하지 않습니다: {0}"), FText::FromString(Url));
		UE_LOG(LogJokateSourceControl, Warning, TEXT("요청 실패: %s"), *Url);
		return Result;
	}

	Result.StatusCode = StatusCode;

	if (!ResponseText.IsEmpty())
	{
		TSharedPtr<FJsonObject> Root;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ResponseText);
		if (FJsonSerializer::Deserialize(Reader, Root) && Root.IsValid())
		{
			Result.Json = Root;
		}
		else
		{
			// 최상위가 배열인 응답(/api/history)도 받아들인다.
			TArray<TSharedPtr<FJsonValue>> RootArray;
			const TSharedRef<TJsonReader<>> ArrayReader = TJsonReaderFactory<>::Create(ResponseText);
			if (FJsonSerializer::Deserialize(ArrayReader, RootArray))
			{
				Result.JsonArray = RootArray;
			}
		}
	}

	if (StatusCode >= 200 && StatusCode < 300)
	{
		Result.bOk = true;
	}
	else
	{
		FString ServerError;
		if (Result.Json.IsValid())
		{
			Result.Json->TryGetStringField(TEXT("error"), ServerError);
		}
		Result.ErrorText = FText::Format(
			LOCTEXT("JokateHttpStatus", "Jokate 데몬 오류 ({0}): {1}"),
			FText::AsNumber(StatusCode), FText::FromString(ServerError.IsEmpty() ? Url : ServerError));
	}

	return Result;
}

#undef LOCTEXT_NAMESPACE
